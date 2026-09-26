"""
Groq Queue Service — Centralized Async Priority Queue & Worker with Retry Mechanism.

Guarantees:
1. Strictly throttled & sequenced Groq API execution (one-by-one or controlled concurrency)
   to eliminate HTTP 429 "Too Many Requests" rate limiting.
2. 3-attempt Enqueue Retry Mechanism (if adding to queue encounters temporary contention or errors).
3. 3-attempt Execution Retry Mechanism with exponential backoff on HTTP 429, timeouts, and 5xx errors.
4. Priority scheduling: Interactive queries (Chats, Real-time Q&A, Questionnaire Validations)
   are scheduled ahead of heavy batch background tasks (Audio transcription, PDF OCR).
5. Comprehensive metrics and queue status tracking.
"""

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
import io
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
import uuid

import httpx

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqPriority(IntEnum):
    """
    Priority levels for Groq requests.
    Lower numerical values indicate higher execution priority.
    """
    CRITICAL = 0
    HIGH = 1      # Interactive chats, question validations, live Q&A
    NORMAL = 2    # Plan generation, document summarization, medical report uploads
    LOW = 3       # Batch transcriptions, background summaries


@dataclass
class GroqJob:
    """Represents a queued Groq API request."""
    job_id: str
    priority: int
    caller: str
    fn: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)


class GroqRateLimitError(Exception):
    """Raised when Groq API responds with HTTP 429."""
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class GroqQueueService:
    """
    Centralized Queue & Sequential Worker for all Groq API calls.
    Ensures that calls across all services are safely serialized with automatic retries.
    """

    def __init__(
        self,
        max_size: int = 500,
        inter_request_delay: float = 0.25,
        default_enqueue_retries: int = 3,
        default_exec_retries: int = 3,
    ):
        self.max_size = max_size
        self.inter_request_delay = inter_request_delay
        self.default_enqueue_retries = default_enqueue_retries
        self.default_exec_retries = default_exec_retries

        self._queue: Optional[asyncio.PriorityQueue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._sequence_counter: int = 0
        self._lock: Optional[asyncio.Lock] = None

        # Telemetry & Metrics
        self.total_enqueued: int = 0
        self.total_processed: int = 0
        self.total_failed: int = 0
        self.total_retries: int = 0

    def _init_internals(self) -> None:
        """Lazily initialize asyncio queue and synchronization primitives on current event loop."""
        if self._queue is None:
            self._queue = asyncio.PriorityQueue(maxsize=self.max_size)
        if self._lock is None:
            self._lock = asyncio.Lock()

    def _get_next_seq(self) -> int:
        """Monotonically increasing sequence number to break priority ties in FIFO order."""
        self._sequence_counter += 1
        return self._sequence_counter

    async def start_worker(self) -> None:
        """Start the background worker task if not already running."""
        self._init_internals()
        if self._running and self._worker_task and not self._worker_task.done():
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="groq_queue_worker")
        logger.info("[GROQ_QUEUE] Background worker started successfully.")

    async def stop_worker(self) -> None:
        """Gracefully stop the background worker task."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("[GROQ_QUEUE] Background worker stopped.")

    async def ensure_worker(self) -> None:
        """Ensures that the internal queue and background worker are active on the current loop."""
        self._init_internals()
        if not self._running or self._worker_task is None or self._worker_task.done():
            await self.start_worker()

    # ── Job Submission & Enqueue Retries ──────────────────────────────────────

    async def submit(
        self,
        fn: Callable[[], Awaitable[Any]],
        priority: int = GroqPriority.NORMAL,
        max_retries: Optional[int] = None,
        enqueue_retries: Optional[int] = None,
        caller: str = "Unknown",
    ) -> Any:
        """
        Submits an async Groq operation into the queue and awaits its turn.

        Features:
        - Retries up to 3 times if putting the task into the queue encounters temporary failure.
        - The worker executes the task one-by-one and retries up to 3 times if Groq returns 429/5xx/timeout.
        """
        await self.ensure_worker()

        actual_max_retries = max_retries if max_retries is not None else self.default_exec_retries
        actual_enqueue_retries = enqueue_retries if enqueue_retries is not None else self.default_enqueue_retries

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        job_id = str(uuid.uuid4())[:8]
        job = GroqJob(
            job_id=job_id,
            priority=int(priority),
            caller=caller,
            fn=fn,
            future=future,
            max_retries=actual_max_retries,
        )

        enqueued = False
        last_enqueue_err: Optional[Exception] = None

        # ── 3-Attempt Enqueue Mechanism ──
        for attempt in range(1, actual_enqueue_retries + 1):
            try:
                seq = self._get_next_seq()
                # 5 second timeout to enqueue if queue is full
                await asyncio.wait_for(self._queue.put((int(priority), seq, job)), timeout=5.0)
                enqueued = True
                self.total_enqueued += 1
                logger.info(
                    f"[GROQ_QUEUE_ENQUEUED] job_id={job_id} caller={caller} "
                    f"priority={priority} attempt={attempt}/{actual_enqueue_retries} "
                    f"queue_depth={self._queue.qsize()}"
                )
                break
            except Exception as exc:
                last_enqueue_err = exc
                logger.warning(
                    f"[GROQ_QUEUE_ENQUEUE_RETRY] Attempt {attempt}/{actual_enqueue_retries} "
                    f"failed to enqueue job {job_id} for {caller}: {exc}"
                )
                if attempt < actual_enqueue_retries:
                    await asyncio.sleep(0.15 * (2 ** (attempt - 1)))

        if not enqueued:
            self.total_failed += 1
            logger.error(
                f"[GROQ_QUEUE_ENQUEUE_EXHAUSTED] Failed to enqueue job for {caller} "
                f"after {actual_enqueue_retries} attempts: {last_enqueue_err}"
            )
            raise ValidationError(
                f"The AI processing queue is temporarily unavailable after {actual_enqueue_retries} attempts. "
                "Please try again."
            )

        # Wait until the worker executes this job and sets the result on the future
        return await future

    # ── Worker Loop & Execution Retries ──────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Background coroutine that sequentially dequeues and processes Groq jobs."""
        logger.info("[GROQ_QUEUE_WORKER] Processing loop started.")
        while self._running:
            try:
                priority, seq, job = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[GROQ_QUEUE_WORKER_ERROR] Unexpected queue get error: {e}")
                await asyncio.sleep(0.5)
                continue

            try:
                logger.info(
                    f"[GROQ_QUEUE_START] job_id={job.job_id} caller={job.caller} "
                    f"priority={priority} remaining_in_queue={self._queue.qsize()}"
                )
                result = await self._execute_with_retries(job)
                self.total_processed += 1
                if not job.future.done():
                    job.future.set_result(result)
                logger.info(f"[GROQ_QUEUE_SUCCESS] job_id={job.job_id} caller={job.caller}")
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                break
            except Exception as exc:
                self.total_failed += 1
                logger.error(f"[GROQ_QUEUE_ERROR] job_id={job.job_id} caller={job.caller} failed: {exc}")
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()
                # Inter-request delay ensures Groq rate limits (token bucket) do not burst
                if self.inter_request_delay > 0:
                    await asyncio.sleep(self.inter_request_delay)

    async def _execute_with_retries(self, job: GroqJob) -> Any:
        """
        Executes a job's async function with up to `job.max_retries` retries
        with exponential backoff and 429 Retry-After header parsing.
        """
        max_retries = job.max_retries
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                return await job.fn()
            except Exception as exc:
                last_exc = exc
                is_retryable = self._is_retryable_error(exc)
                if not is_retryable or attempt >= max_retries:
                    logger.error(
                        f"[GROQ_EXEC_FAILED_FINAL] job_id={job.job_id} caller={job.caller} "
                        f"attempt={attempt + 1}/{max_retries + 1} retryable={is_retryable} error={exc}"
                    )
                    raise exc

                self.total_retries += 1
                backoff = self._calculate_backoff(attempt, exc)
                logger.warning(
                    f"[GROQ_EXEC_RETRY] job_id={job.job_id} caller={job.caller} "
                    f"attempt={attempt + 1}/{max_retries + 1} failed with {type(exc).__name__}: {exc}. "
                    f"Waiting {backoff:.2f}s before retry..."
                )
                await asyncio.sleep(backoff)

        if last_exc:
            raise last_exc

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        """Determines if the encountered error qualifies for execution retry."""
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout)):
            return True
        if isinstance(exc, GroqRateLimitError):
            return True

        # Check HTTP response status codes if wrapped
        status_code = getattr(exc, "status_code", None)
        if status_code in (429, 500, 502, 503, 504):
            return True

        err_msg = str(exc).lower()
        if "429" in err_msg or "rate limit" in err_msg or "too many requests" in err_msg:
            return True
        if "timeout" in err_msg or "temporarily busy" in err_msg or "service unavailable" in err_msg:
            return True

        return False

    @staticmethod
    def _calculate_backoff(attempt: int, exc: Exception) -> float:
        """Calculates backoff delay, respecting Groq 429 Retry-After if provided."""
        if isinstance(exc, GroqRateLimitError) and exc.retry_after and exc.retry_after > 0:
            return exc.retry_after + 0.5

        # Exponential backoff: 2s, 4s, 8s with small jitter
        base_backoff = 2.0 ** (attempt + 1)
        jitter = (attempt * 0.25)
        return min(base_backoff + jitter, 15.0)

    # ── High-Level Convenience Methods ────────────────────────────────────────

    async def submit_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        priority: int = GroqPriority.NORMAL,
        caller: str = "ChatCompletion",
        timeout: float = 90.0,
        enqueue_retries: int = 3,
        max_retries: int = 3,
    ) -> str:
        """
        Dispatches a Chat Completions call through the Groq queue with cleanup of reasoning tags.
        """
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError(
                "Groq API key is not configured. Please set GROQ_API or GROQ_API_KEY in backend .env."
            )

        model_name = model or settings.GROQ_MODEL or "llama-3.3-70b-versatile"
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def _call_api() -> str:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(GROQ_CHAT_COMPLETIONS_URL, json=payload, headers=headers)

                if resp.status_code == 429:
                    retry_header = resp.headers.get("retry-after")
                    retry_val = float(retry_header) if retry_header and retry_header.isdigit() else 2.5
                    raise GroqRateLimitError(f"Groq API rate limit exceeded (429): {resp.text[:200]}", retry_val)

                if resp.status_code != 200:
                    err_text = resp.text[:300]
                    logger.error(f"[GROQ_API_ERR] status={resp.status_code} caller={caller}: {err_text}")
                    if resp.status_code >= 500:
                        raise httpx.HTTPStatusError(f"Groq 5xx Server Error ({resp.status_code})", request=resp.request, response=resp)
                    raise ValidationError(f"Groq AI service error ({resp.status_code}): {err_text}")

                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValidationError("Groq AI returned an empty response.")

                raw_content = choices[0].get("message", {}).get("content", "").strip()
                # Clean <think>...</think> tags if reasoning model
                cleaned_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
                if "<think>" in cleaned_content and "</think>" not in cleaned_content:
                    cleaned_content = cleaned_content.split("<think>", 1)[0].strip()

                return cleaned_content or raw_content

        return await self.submit(
            fn=_call_api,
            priority=priority,
            max_retries=max_retries,
            enqueue_retries=enqueue_retries,
            caller=caller,
        )

    async def submit_transcription(
        self,
        audio_path: str,
        model: Optional[str] = None,
        language: Optional[str] = None,
        priority: int = GroqPriority.NORMAL,
        caller: str = "WhisperTranscription",
        timeout: float = 120.0,
        enqueue_retries: int = 3,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Dispatches an audio transcription call through the Groq queue.
        """
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError("Groq API key is not configured.")

        whisper_model = model or settings.GROQ_WHISPER_MODEL or "whisper-large-v3-turbo"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        if not os.path.exists(audio_path):
            raise ValidationError(f"Audio file not found at path: {audio_path}")

        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        filename = os.path.basename(audio_path)
        data = {
            "model": whisper_model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language

        async def _call_whisper() -> Dict[str, Any]:
            files = {
                "file": (filename, audio_bytes),
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    GROQ_WHISPER_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )

                if response.status_code == 429:
                    retry_header = response.headers.get("retry-after")
                    retry_val = float(retry_header) if retry_header and retry_header.isdigit() else 3.0
                    raise GroqRateLimitError(f"Groq Whisper rate limited (429)", retry_val)

                if response.status_code != 200:
                    err_text = response.text[:400]
                    logger.error(f"[WHISPER_API_ERR] status={response.status_code} file={filename}: {err_text}")
                    if response.status_code >= 500:
                        raise httpx.HTTPStatusError(f"Groq 5xx Server Error ({response.status_code})", request=response.request, response=response)
                    raise ValidationError(f"Groq Whisper API error ({response.status_code}): {err_text}")

                return response.json()

        return await self.submit(
            fn=_call_whisper,
            priority=priority,
            max_retries=max_retries,
            enqueue_retries=enqueue_retries,
            caller=caller,
        )

    def get_status(self) -> Dict[str, Any]:
        """Returns current operational telemetry of the Groq queue."""
        q_size = self._queue.qsize() if self._queue is not None else 0
        return {
            "worker_running": self._running,
            "current_queue_depth": q_size,
            "total_enqueued": self.total_enqueued,
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "total_retries": self.total_retries,
            "inter_request_delay_seconds": self.inter_request_delay,
        }


# Global Singleton Instance
groq_queue = GroqQueueService()

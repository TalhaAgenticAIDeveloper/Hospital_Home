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
        concurrency: int = 1,
        inter_request_delay: float = 0.0,
        default_enqueue_retries: int = 3,
        default_exec_retries: int = 3,
    ):
        self.max_size = max_size
        self.concurrency = concurrency
        self.inter_request_delay = inter_request_delay
        self.default_enqueue_retries = default_enqueue_retries
        self.default_exec_retries = default_exec_retries

        self._queue: Optional[asyncio.PriorityQueue] = None
        self._worker_tasks: List[asyncio.Task] = []
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
        """Start the background worker tasks if not already running."""
        self._init_internals()
        active_tasks = [t for t in self._worker_tasks if not t.done()]
        if self._running and len(active_tasks) == self.concurrency:
            return

        self._running = True
        self._worker_tasks = []
        for i in range(self.concurrency):
            t = asyncio.create_task(self._worker_loop(worker_id=i), name=f"groq_queue_worker_{i}")
            self._worker_tasks.append(t)
        logger.info(f"[GROQ_QUEUE] Background worker pool started ({self.concurrency} concurrent workers).")

    async def stop_worker(self) -> None:
        """Gracefully stop all background worker tasks."""
        self._running = False
        for t in self._worker_tasks:
            if not t.done():
                t.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        logger.info("[GROQ_QUEUE] Background worker pool stopped.")

    async def ensure_worker(self) -> None:
        """Ensures that the internal queue and background workers are active on the current loop."""
        self._init_internals()
        active_tasks = [t for t in self._worker_tasks if not t.done()]
        if not self._running or len(active_tasks) < self.concurrency:
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

    async def _worker_loop(self, worker_id: int = 0) -> None:
        """Background coroutine that dequeues and processes Groq jobs concurrently."""
        logger.info(f"[GROQ_QUEUE_WORKER_{worker_id}] Processing loop started.")
        while self._running:
            try:
                priority, seq, job = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[GROQ_QUEUE_WORKER_{worker_id}_ERROR] Unexpected queue get error: {e}")
                await asyncio.sleep(0.1)
                continue

            try:
                logger.info(
                    f"[GROQ_QUEUE_START] worker={worker_id} job_id={job.job_id} caller={job.caller} "
                    f"priority={priority} remaining_in_queue={self._queue.qsize()}"
                )
                result = await self._execute_with_retries(job)
                self.total_processed += 1
                if not job.future.done():
                    job.future.set_result(result)
                logger.info(f"[GROQ_QUEUE_SUCCESS] worker={worker_id} job_id={job.job_id} caller={job.caller}")
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                break
            except Exception as exc:
                self.total_failed += 1
                logger.error(f"[GROQ_QUEUE_ERROR] worker={worker_id} job_id={job.job_id} caller={job.caller} failed: {exc}")
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()
                if self.inter_request_delay > 0:
                    await asyncio.sleep(self.inter_request_delay)

    async def _execute_with_retries(self, job: GroqJob) -> Any:
        """
        Executes a job's async function with up to `job.max_retries` retries
        with fast exponential backoff and rate-limit delay capping.
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
        err_msg = str(exc).lower()

        # Hard daily token limits / quota exhaustion cannot succeed in seconds — fail immediately!
        if "tokens per day" in err_msg or "tpd" in err_msg or "daily limit" in err_msg or "service tier" in err_msg:
            return False

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout)):
            return True
        if isinstance(exc, GroqRateLimitError):
            return True

        status_code = getattr(exc, "status_code", None)
        if status_code in (429, 500, 502, 503, 504):
            return True

        if "429" in err_msg or "rate limit" in err_msg or "too many requests" in err_msg:
            return True
        if "timeout" in err_msg or "temporarily busy" in err_msg or "service unavailable" in err_msg:
            return True

        return False

    @staticmethod
    def _calculate_backoff(attempt: int, exc: Exception) -> float:
        """Calculates backoff delay, strictly capped to prevent blocking workers."""
        if isinstance(exc, GroqRateLimitError) and exc.retry_after and exc.retry_after > 0:
            # Never block a worker for more than 3 seconds
            return min(exc.retry_after, 3.0)

        # Fast backoff: 0.2s, 0.4s, 0.8s
        return min(0.2 * (2.0 ** attempt), 1.5)

    # ── High-Level Convenience Methods ────────────────────────────────────────

    async def submit_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        priority: int = GroqPriority.NORMAL,
        caller: str = "ChatCompletion",
        timeout: float = 45.0,
        enqueue_retries: int = 3,
        max_retries: int = 3,
    ) -> str:
        """
        Dispatches a Chat Completions call through the Groq queue with cleanup of reasoning tags
        and automatic lightweight model fallback if primary model quota is exhausted.
        """
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError(
                "Groq API key is not configured. Please set GROQ_API or GROQ_API_KEY in backend .env."
            )

        primary_model = model or settings.GROQ_MODEL or "openai/gpt-oss-20b"
        # Fast fallback models available on this API key
        candidate_models = [primary_model]
        for fallback in ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def _call_api() -> str:
            last_model_err: Optional[Exception] = None

            for cur_model in candidate_models:
                cur_payload = {
                    "model": cur_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        resp = await client.post(GROQ_CHAT_COMPLETIONS_URL, json=cur_payload, headers=headers)

                        if resp.status_code == 429:
                            err_body = resp.text.lower()
                            # If daily limit / TPD, immediately switch to next model candidate!
                            if "tokens per day" in err_body or "tpd" in err_body or "daily limit" in err_body:
                                logger.warning(
                                    f"[GROQ_TPD_QUOTA_EXCEEDED] Model {cur_model} daily quota exhausted, "
                                    f"attempting fast fallback model..."
                                )
                                last_model_err = GroqRateLimitError(f"Daily quota exceeded for {cur_model}", 0.0)
                                continue

                            retry_header = resp.headers.get("retry-after")
                            retry_val = float(retry_header) if retry_header and retry_header.isdigit() else 1.0
                            retry_val = min(retry_val, 3.0)
                            raise GroqRateLimitError(f"Groq API rate limit exceeded (429): {resp.text[:200]}", retry_val)

                        if resp.status_code != 200:
                            err_text = resp.text[:300]
                            # If model not found or decommissioned, try next candidate
                            if resp.status_code in (400, 404) and ("not exist" in err_text or "decommissioned" in err_text):
                                logger.warning(f"[GROQ_MODEL_UNAVAILABLE] Model {cur_model} unavailable, trying fallback: {err_text}")
                                last_model_err = ValidationError(f"Model unavailable: {err_text}")
                                continue

                            logger.error(f"[GROQ_API_ERR] status={resp.status_code} model={cur_model} caller={caller}: {err_text}")
                            if resp.status_code >= 500:
                                raise httpx.HTTPStatusError(f"Groq 5xx Server Error ({resp.status_code})", request=resp.request, response=resp)
                            raise ValidationError(f"Groq AI service error ({resp.status_code}): {err_text}")

                        data = resp.json()
                        choices = data.get("choices", [])
                        if not choices:
                            raise ValidationError("Groq AI returned an empty response.")

                        raw_content = choices[0].get("message", {}).get("content", "").strip()
                        cleaned_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
                        if "<think>" in cleaned_content and "</think>" not in cleaned_content:
                            cleaned_content = cleaned_content.split("<think>", 1)[0].strip()

                        return cleaned_content or raw_content

                except GroqRateLimitError as rle:
                    # If quota exhausted (retry_after == 0.0), continue to next model candidate
                    if rle.retry_after == 0.0:
                        continue
                    raise rle

            if last_model_err:
                raise last_model_err
            raise ValidationError("All candidate Groq AI models failed.")

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
        active_worker_count = len([t for t in self._worker_tasks if not t.done()])
        return {
            "worker_running": self._running,
            "concurrency": self.concurrency,
            "active_workers": active_worker_count,
            "current_queue_depth": q_size,
            "total_enqueued": self.total_enqueued,
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "total_retries": self.total_retries,
            "inter_request_delay_seconds": self.inter_request_delay,
        }


# Global Singleton Instance
groq_queue = GroqQueueService()

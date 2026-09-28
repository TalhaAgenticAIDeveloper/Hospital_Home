"""
Gemini Live Transcription Service — real-time speech-to-text via Gemini Live API.

Uses the google-genai SDK to open a persistent WebSocket session with Google's
Gemini 3.5 Transcribe Live model. Audio chunks (16-bit PCM, 16 kHz, mono) are
streamed in and transcribed text segments are yielded back in real-time.

This service is used per-participant: each doctor/patient connection in a
meeting room gets its own Gemini Live session so that speaker attribution
is accurate and reliable.
"""

import asyncio
import base64
import time
import uuid
from typing import Any, Callable, Coroutine, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class GeminiLiveSession:
    """
    Manages a single Gemini Live Transcription session for one participant.

    Lifecycle:
        1. connect()            — opens the Gemini Live WebSocket session
        2. send_audio(chunk)    — streams raw PCM audio data to Gemini
        3. _receive_loop()      — background task that reads transcribed text
        4. close()              — gracefully shuts down everything
    """

    def __init__(
        self,
        room_id: str,
        user_id: str,
        role: str,          # 'doctor' or 'patient'
        speaker_name: str,
        on_transcript: Callable[..., Coroutine],
    ):
        self.room_id = room_id
        self.user_id = user_id
        self.role = role
        self.speaker_name = speaker_name
        self.on_transcript = on_transcript   # async callback(segment_dict)

        self._session = None
        self._client = None
        self._receive_task: Optional[asyncio.Task] = None
        self._connected = False
        self._session_start_time: float = 0.0
        self._chunk_count = 0

    async def connect(self) -> bool:
        """
        Open the Gemini Live session.
        Returns True if connected successfully, False otherwise.
        """
        api_key = settings.GEMINI_API_KEY
        model = settings.GEMINI_MODEL

        if not api_key:
            logger.error(
                f"[GEMINI_LIVE] Cannot start session for {self.role} in room {self.room_id}: "
                "GEMINI_API_KEY is not configured"
            )
            return False

        try:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=api_key)

            # Configure for transcription-only (TEXT output, no audio response)
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                input_audio_transcription=types.AudioTranscriptionConfig(),
            )

            logger.info(
                f"[GEMINI_LIVE] Connecting to Gemini Live: model={model} "
                f"room={self.room_id} role={self.role} user={self.user_id}"
            )

            self._session = await self._client.aio.live.connect(
                model=model,
                config=config,
            )

            # The context manager returned a session object we can use
            self._connected = True
            self._session_start_time = time.time()
            self._chunk_count = 0

            # Start background receive loop
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"gemini-rx-{self.room_id}-{self.role}",
            )

            logger.info(
                f"[GEMINI_LIVE] Session CONNECTED: room={self.room_id} role={self.role}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[GEMINI_LIVE] Failed to connect: room={self.room_id} "
                f"role={self.role} error={e}",
                exc_info=True,
            )
            self._connected = False
            return False

    async def send_audio(self, audio_data: bytes) -> None:
        """
        Stream a chunk of raw PCM audio (16-bit, 16 kHz, mono) to Gemini.
        Audio data should be raw bytes (not base64 encoded).
        """
        if not self._connected or not self._session:
            return

        try:
            from google.genai import types

            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000",
                ),
            )
            self._chunk_count += 1

            if self._chunk_count % 100 == 0:
                elapsed = time.time() - self._session_start_time
                logger.debug(
                    f"[GEMINI_LIVE] Audio streaming: room={self.room_id} "
                    f"role={self.role} chunks={self._chunk_count} "
                    f"elapsed={elapsed:.1f}s"
                )

        except Exception as e:
            logger.warning(
                f"[GEMINI_LIVE] Send audio error: room={self.room_id} "
                f"role={self.role} error={e}"
            )

    async def _receive_loop(self) -> None:
        """
        Background coroutine that continuously reads transcription responses
        from the Gemini Live session and invokes the on_transcript callback.
        """
        if not self._session:
            return

        try:
            while self._connected:
                try:
                    async for response in self._session.receive():
                        if not self._connected:
                            break

                        text = self._extract_text(response)
                        if text and text.strip():
                            elapsed = time.time() - self._session_start_time
                            segment = {
                                "id": f"{self.role}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
                                "speaker": self.role,
                                "participant": self.role,
                                "speakerName": self.speaker_name,
                                "text": text.strip(),
                                "is_final": True,
                                "timestamp": round(elapsed, 2),
                                "start_time": round(elapsed, 2),
                                "lang": "auto",
                            }

                            logger.info(
                                f"[GEMINI_LIVE] Transcription: room={self.room_id} "
                                f"[{self.role.upper()}] \"{text.strip()}\" "
                                f"at {elapsed:.1f}s"
                            )

                            try:
                                await self.on_transcript(segment)
                            except Exception as cb_err:
                                logger.warning(
                                    f"[GEMINI_LIVE] Callback error: {cb_err}"
                                )

                except StopAsyncIteration:
                    # Session receive iterator ended — Gemini closed the stream
                    logger.info(
                        f"[GEMINI_LIVE] Receive stream ended: room={self.room_id} "
                        f"role={self.role}"
                    )
                    break

                except Exception as rx_err:
                    if not self._connected:
                        break
                    logger.warning(
                        f"[GEMINI_LIVE] Receive error (will retry): "
                        f"room={self.room_id} role={self.role} error={rx_err}"
                    )
                    await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.debug(
                f"[GEMINI_LIVE] Receive loop cancelled: room={self.room_id} "
                f"role={self.role}"
            )
        except Exception as e:
            logger.error(
                f"[GEMINI_LIVE] Receive loop fatal error: room={self.room_id} "
                f"role={self.role} error={e}",
                exc_info=True,
            )

    @staticmethod
    def _extract_text(response: Any) -> Optional[str]:
        """
        Extract transcription text from a Gemini Live API response.

        The response structure can vary:
        - response.server_content.input_transcription.text  (what user said)
        - response.server_content.model_turn.parts[0].text  (model output)
        - response.text  (convenience accessor)
        - response.data  (raw string data)
        """
        try:
            # Primary: input audio transcription (what the user said)
            sc = getattr(response, "server_content", None)
            if sc:
                # Input transcription (user's speech → text)
                it = getattr(sc, "input_transcription", None)
                if it:
                    text = getattr(it, "text", None)
                    if text and text.strip():
                        return text.strip()

                # Model turn (model's text response)
                mt = getattr(sc, "model_turn", None)
                if mt:
                    parts = getattr(mt, "parts", None)
                    if parts:
                        for part in parts:
                            text = getattr(part, "text", None)
                            if text and text.strip():
                                return text.strip()

            # Convenience accessors
            text = getattr(response, "text", None)
            if text and text.strip():
                return text.strip()

            data = getattr(response, "data", None)
            if data and isinstance(data, str) and data.strip():
                return data.strip()

        except Exception:
            pass

        return None

    async def close(self) -> None:
        """Gracefully shut down the Gemini Live session."""
        self._connected = False

        # Cancel receive task
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await asyncio.wait_for(self._receive_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._receive_task = None

        # Close the Gemini session
        if self._session:
            try:
                await self._session.close()
            except Exception as e:
                logger.debug(
                    f"[GEMINI_LIVE] Session close notice: {e}"
                )
            self._session = None

        elapsed = time.time() - self._session_start_time if self._session_start_time else 0
        logger.info(
            f"[GEMINI_LIVE] Session CLOSED: room={self.room_id} "
            f"role={self.role} chunks_sent={self._chunk_count} "
            f"duration={elapsed:.1f}s"
        )

    @property
    def is_connected(self) -> bool:
        return self._connected


class GeminiLiveTranscriptionManager:
    """
    Manages all active Gemini Live transcription sessions across meeting rooms.

    Each participant (doctor/patient) in a meeting room gets their own
    GeminiLiveSession so that speaker attribution is server-enforced.

    Usage from the WebSocket endpoint:
        session = await manager.create_session(room_id, user_id, role, name, callback)
        await session.send_audio(chunk)
        await manager.close_session(room_id, user_id)
    """

    def __init__(self):
        # Key: f"{room_id}:{user_id}" → GeminiLiveSession
        self._sessions: dict[str, GeminiLiveSession] = {}

    def _key(self, room_id: str, user_id: str) -> str:
        return f"{room_id}:{user_id}"

    async def create_session(
        self,
        room_id: str,
        user_id: str,
        role: str,
        speaker_name: str,
        on_transcript: Callable[..., Coroutine],
    ) -> Optional[GeminiLiveSession]:
        """
        Create and connect a new Gemini Live session for a participant.
        Closes any existing session for the same user in the same room first.
        """
        key = self._key(room_id, user_id)

        # Close existing session if any (e.g., page refresh)
        if key in self._sessions:
            old = self._sessions.pop(key)
            await old.close()

        session = GeminiLiveSession(
            room_id=room_id,
            user_id=user_id,
            role=role,
            speaker_name=speaker_name,
            on_transcript=on_transcript,
        )

        connected = await session.connect()
        if not connected:
            logger.warning(
                f"[GEMINI_MANAGER] Failed to create session: "
                f"room={room_id} role={role}"
            )
            return None

        self._sessions[key] = session
        logger.info(
            f"[GEMINI_MANAGER] Session created: room={room_id} "
            f"role={role} active_sessions={len(self._sessions)}"
        )
        return session

    def get_session(self, room_id: str, user_id: str) -> Optional[GeminiLiveSession]:
        """Get an existing session for a participant."""
        return self._sessions.get(self._key(room_id, user_id))

    async def close_session(self, room_id: str, user_id: str) -> None:
        """Close and remove a specific participant's session."""
        key = self._key(room_id, user_id)
        session = self._sessions.pop(key, None)
        if session:
            await session.close()

    async def close_room(self, room_id: str) -> None:
        """Close all sessions in a room (e.g., meeting ended)."""
        keys_to_remove = [k for k in self._sessions if k.startswith(f"{room_id}:")]
        for key in keys_to_remove:
            session = self._sessions.pop(key, None)
            if session:
                await session.close()

        if keys_to_remove:
            logger.info(
                f"[GEMINI_MANAGER] Room closed: room={room_id} "
                f"sessions_closed={len(keys_to_remove)}"
            )

    async def close_all(self) -> None:
        """Close all active sessions (shutdown)."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)


# ── Global singleton ────────────────────────────────────────────────────────
gemini_transcription_manager = GeminiLiveTranscriptionManager()

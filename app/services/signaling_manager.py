"""
WebRTC Signaling Manager for 1-to-1 Video Consultations.
"""

import json
from typing import Dict, List, Optional
from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class MeetingConnection:
    """Represents a connected participant in a meeting room."""
    def __init__(self, websocket: WebSocket, user_id: str, role: str, name: str):
        self.websocket = websocket
        self.user_id = user_id
        self.role = role
        self.name = name


class SignalingManager:
    """
    Manages active WebRTC signaling connections per meeting room
    (offer, answer, ice-candidates, peer-joined, peer-left, meeting-ended).
    """

    def __init__(self):
        # room_id -> List[MeetingConnection]
        self._rooms: Dict[str, List[MeetingConnection]] = {}
        # room_id -> List[dict] of live transcript segments
        self._transcript_buffers: Dict[str, List[dict]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        room_id: str,
        user_id: str,
        role: str,
        name: str,
    ) -> bool:
        """
        Accept and register a participant into a meeting room.
        Enforces maximum 2 participants per 1-to-1 room.
        """
        await websocket.accept()

        if room_id not in self._rooms:
            self._rooms[room_id] = []

        # Remove stale connection for same user or same role first (e.g. page refresh / duplicate tab)
        stale = [c for c in self._rooms[room_id] if c.user_id == user_id or c.role == role]
        for old_conn in stale:
            if old_conn in self._rooms[room_id]:
                self._rooms[room_id].remove(old_conn)
            logger.info(f"Removed stale connection for user {user_id} ({role}) in room {room_id}")
            try:
                await old_conn.websocket.close(code=4008, reason="Replaced by new connection")
            except Exception:
                pass

        # Check room capacity (max 2: 1 doctor, 1 patient)
        if len(self._rooms[room_id]) >= 2:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "Meeting room is full. Maximum 2 participants allowed.",
            }))
            await websocket.close(code=4003)
            return False

        connection = MeetingConnection(websocket, user_id, role, name)
        self._rooms[room_id].append(connection)

        logger.info(f"User {user_id} ({role}: {name}) joined room {room_id}. Total: {len(self._rooms[room_id])}")

        # Notify the other participant that a peer has joined
        await self.broadcast(
            room_id,
            {
                "type": "peer-joined",
                "user_id": user_id,
                "role": role,
                "name": name,
                "peer_count": len(self._rooms[room_id]),
            },
            exclude=websocket,
        )

        # Notify the joining user of current room state
        await websocket.send_text(json.dumps({
            "type": "room-status",
            "peer_count": len(self._rooms[room_id]),
            "participants": [
                {"user_id": c.user_id, "role": c.role, "name": c.name}
                for c in self._rooms[room_id]
            ],
        }))

        return True

    def disconnect(self, websocket: WebSocket, room_id: str):
        """Remove participant from room and notify peers."""
        if room_id in self._rooms:
            leaving_conn = None
            for conn in self._rooms[room_id]:
                if conn.websocket == websocket:
                    leaving_conn = conn
                    break

            if leaving_conn:
                self._rooms[room_id].remove(leaving_conn)
                logger.info(f"User {leaving_conn.user_id} ({leaving_conn.role}) left room {room_id}")

            if not self._rooms[room_id]:
                del self._rooms[room_id]
                import asyncio
                asyncio.create_task(self._persist_transcript_buffer(room_id, is_final=True))

    def get_connection(self, room_id: str, websocket: WebSocket) -> Optional[MeetingConnection]:
        """Find the MeetingConnection for a given WebSocket in a room."""
        if room_id in self._rooms:
            for conn in self._rooms[room_id]:
                if conn.websocket == websocket:
                    return conn
        return None

    async def broadcast(self, room_id: str, message: dict, exclude: Optional[WebSocket] = None):
        """Broadcast a message to participants in room, optionally excluding sender."""
        if room_id not in self._rooms:
            return

        payload = json.dumps(message)
        for conn in list(self._rooms[room_id]):
            if conn.websocket != exclude:
                try:
                    await conn.websocket.send_text(payload)
                except Exception as e:
                    logger.warning(f"Failed to send to participant {conn.user_id}: {e}")

    async def handle_message(self, room_id: str, sender_ws: WebSocket, raw_text: str):
        """Process WebRTC signaling, live speech recognition transcript segments, or meeting control message."""
        try:
            data = json.loads(raw_text)
        except Exception:
            return

        msg_type = data.get("type")

        # ── Real-Time Web Speech Live Transcript Segment ──────────────────────
        if msg_type == "transcript-segment":
            connection = self.get_connection(room_id, sender_ws)
            if connection:
                # 1. Enforce authenticated speaker identity from server-verified connection
                data["speaker"] = connection.role
                data["participant"] = connection.role
                data["speakerName"] = connection.name

                # 2. Broadcast segment to the other participant in the room
                await self.broadcast(room_id, data, exclude=sender_ws)

                # 3. If final segment, append to room buffer and persist to DB
                if data.get("is_final", False) and data.get("text", "").strip():
                    self._append_transcript_segment(room_id, data)
            return

        # ── WebRTC Signaling & Custom In-Meeting Events ──────────────────────
        elif msg_type in ("offer", "answer", "ice-candidate", "documents-updated", "doc-summary-update"):
            await self.broadcast(room_id, data, exclude=sender_ws)

        # ── Meeting Ended Signal ─────────────────────────────────────────────
        elif msg_type == "meeting-ended":
            import asyncio
            asyncio.create_task(self._persist_transcript_buffer(room_id, is_final=True))

            await self.broadcast(room_id, {
                "type": "meeting-ended",
                "ended_by": data.get("ended_by", "participant"),
            })

    def _append_transcript_segment(self, room_id: str, data: dict):
        """Store final transcript segment and trigger debounced DB save."""
        import asyncio
        from datetime import datetime, timezone
        import time

        if room_id not in self._transcript_buffers:
            self._transcript_buffers[room_id] = []

        text = data.get("text", "").strip()
        if not text:
            return

        seg_id = data.get("id") or f"{room_id}-{data.get('speaker')}-{int(time.time() * 1000)}"

        # Deduplicate by id or identical speaker+text within 2 seconds
        existing = [
            s for s in self._transcript_buffers[room_id]
            if s.get("id") == seg_id or (s.get("speaker") == data.get("speaker") and s.get("text") == text)
        ]
        if existing:
            return

        segment = {
            "id": seg_id,
            "speaker": data.get("speaker"),
            "participant": data.get("speaker"),
            "speakerName": data.get("speakerName") or ("Doctor" if data.get("speaker") == "doctor" else "Patient"),
            "text": text,
            "timestamp": data.get("timestamp", 0.0),
            "start_time": data.get("start_time", data.get("timestamp", 0.0)),
            "lang": data.get("lang", "en-US"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._transcript_buffers[room_id].append(segment)
        self._transcript_buffers[room_id].sort(key=lambda s: s.get("timestamp", 0.0))

        logger.info(
            f"[LIVE_TRANSCRIPT_SEGMENT] room={room_id} speaker={segment['speaker']} "
            f"text=\"{text}\" total={len(self._transcript_buffers[room_id])}"
        )

        asyncio.create_task(self._persist_transcript_buffer(room_id, is_final=False))

    async def _persist_transcript_buffer(self, room_id: str, is_final: bool = False):
        """Save accumulated live transcript segments to PostgreSQL ConsultationTranscript & Meeting."""
        segments = list(self._transcript_buffers.get(room_id, []))
        if not segments:
            return

        from app.core.database import async_session_maker
        from app.repositories.meeting_repository import MeetingRepository
        from app.repositories.consultation_ai_repository import ConsultationAIRepository
        from app.models.consultation_transcript import ConsultationTranscript

        lines = []
        for s in segments:
            role = (s.get("speaker") or "participant").upper()
            name = s.get("speakerName") or f"[{role}]"
            ts = float(s.get("timestamp", 0.0))
            minutes = int(ts // 60)
            seconds = int(ts % 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {name}: {s.get('text', '')}")
        full_text = "\n".join(lines)

        try:
            async with async_session_maker() as session:
                meeting = await MeetingRepository.get_meeting_by_room_id(session, room_id)
                if not meeting:
                    return

                status_val = "completed" if is_final else "in_progress"
                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(session, meeting.id)
                if not transcript:
                    transcript = ConsultationTranscript(
                        meeting_id=meeting.id,
                        transcription_status=status_val,
                        transcription_model="live-web-speech",
                        structured_transcript=segments,
                        full_text=full_text,
                    )
                    await ConsultationAIRepository.create_transcript(session, transcript)
                else:
                    transcript.structured_transcript = segments
                    transcript.full_text = full_text
                    transcript.transcription_model = "live-web-speech"
                    if is_final or transcript.transcription_status != "completed":
                        transcript.transcription_status = status_val

                meeting.transcript_text = full_text
                await session.commit()
                logger.debug(
                    f"[LIVE_TRANSCRIPT_PERSISTED] room={room_id} segments={len(segments)} status={status_val}"
                )
        except Exception as e:
            logger.warning(f"[LIVE_TRANSCRIPT_PERSIST_WARN] room={room_id}: {e}")

    def get_transcript_segments(self, room_id: str) -> List[dict]:
        """Return buffered transcript segments for a room."""
        return list(self._transcript_buffers.get(room_id, []))

    def clear_transcript_buffer(self, room_id: str):
        """Clear buffered transcript segments for a room."""
        self._transcript_buffers.pop(room_id, None)


# Global singleton instance
signaling_manager = SignalingManager()


"""
WebRTC Signaling and In-Memory Transcript Aggregator for 1-to-1 Video Consultations.
"""

import json
from datetime import datetime, timezone
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
    Manages active WebRTC signaling connections and aggregates real-time
    bilingual (English/Urdu) speech transcripts per meeting room.
    """

    def __init__(self):
        # room_id -> List[MeetingConnection]
        self._rooms: Dict[str, List[MeetingConnection]] = {}
        # room_id -> List[dict] transcript segments
        self._transcripts: Dict[str, List[dict]] = {}

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
            if room_id not in self._transcripts:
                self._transcripts[room_id] = []

        # Check room capacity (max 2: 1 doctor, 1 patient)
        existing_roles = [conn.role for conn in self._rooms[room_id]]
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
            "existing_transcripts": self._transcripts.get(room_id, []),
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
                # Keep transcript buffer for later persistence
                del self._rooms[room_id]

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
        """Process WebRTC signaling or live speech transcription message."""
        try:
            data = json.loads(raw_text)
        except Exception:
            return

        msg_type = data.get("type")

        # ── WebRTC Signaling: Offer / Answer / ICE Candidate ─────────────────
        if msg_type in ("offer", "answer", "ice-candidate"):
            await self.broadcast(room_id, data, exclude=sender_ws)

        # ── Real-Time Bilingual Speech Transcript Segment ────────────────────
        elif msg_type == "transcript-segment":
            segment = {
                "speaker": data.get("speaker", "unknown"),
                "speaker_name": data.get("speaker_name", "Anonymous"),
                "text": data.get("text", "").strip(),
                "timestamp": data.get("timestamp") or datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "language": data.get("language", "en-US"),
            }

            if segment["text"]:
                if room_id not in self._transcripts:
                    self._transcripts[room_id] = []
                self._transcripts[room_id].append(segment)

                # Broadcast live segment to both participants for live subtitles / notes
                await self.broadcast(room_id, {
                    "type": "transcript-segment",
                    "segment": segment,
                })

        # ── Meeting Ended Signal ─────────────────────────────────────────────
        elif msg_type == "meeting-ended":
            await self.broadcast(room_id, {
                "type": "meeting-ended",
                "ended_by": data.get("ended_by", "participant"),
            })

    def get_transcript_segments(self, room_id: str) -> List[dict]:
        """Retrieve all accumulated transcript segments for room."""
        return self._transcripts.get(room_id, [])

    def clear_transcript_buffer(self, room_id: str):
        """Clear memory buffer after persisting to disk/database."""
        self._transcripts.pop(room_id, None)


# Global singleton instance
signaling_manager = SignalingManager()

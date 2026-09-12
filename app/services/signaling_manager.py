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
        """Process WebRTC signaling or meeting control message."""
        try:
            data = json.loads(raw_text)
        except Exception:
            return

        msg_type = data.get("type")

        # ── WebRTC Signaling & Custom In-Meeting Events ──────────────────────
        if msg_type in ("offer", "answer", "ice-candidate", "documents-updated", "doc-summary-update", "transcript-segment"):
            await self.broadcast(room_id, data, exclude=sender_ws)

        # ── Meeting Ended Signal ─────────────────────────────────────────────
        elif msg_type == "meeting-ended":
            await self.broadcast(room_id, {
                "type": "meeting-ended",
                "ended_by": data.get("ended_by", "participant"),
            })

    def get_transcript_segments(self, room_id: str) -> List[dict]:
        """Legacy stub — returns empty list."""
        return []

    def clear_transcript_buffer(self, room_id: str):
        """Legacy stub — no-op."""
        pass


# Global singleton instance
signaling_manager = SignalingManager()

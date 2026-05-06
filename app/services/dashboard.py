"""
Dashboard WebSocket Manager
============================
Manages real-time WebSocket connections for the monitoring dashboard.
Extracted from main.py to avoid circular imports with streams.py.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("dashboard")


class DashboardManager:
    """Manages WebSocket connections for the live dashboard."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"[Dashboard] Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"[Dashboard] Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Send a message to all connected dashboard clients."""
        for connection in self.active_connections.copy():
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

    async def broadcast_transcript(self, speaker: str, text: str):
        """Convenience: broadcast a transcript line."""
        await self.broadcast({"type": "transcript", "speaker": speaker, "text": text})

    async def broadcast_agent_change(self, agent_name: str):
        """Convenience: broadcast an agent change event."""
        await self.broadcast({"type": "agent_change", "agent_name": agent_name})

    async def broadcast_call_summary(self, summary: dict):
        """Convenience: broadcast collected call summary fields."""
        await self.broadcast({"type": "call_summary", **summary})

    async def broadcast_api(self, endpoint: str, status: int, details: Any = ""):
        """Convenience: broadcast an API call result."""
        await self.broadcast({
            "type": "api",
            "endpoint": endpoint,
            "status": status,
            "details": details,
        })


# Singleton — imported by main.py and streams.py
dashboard_manager = DashboardManager()

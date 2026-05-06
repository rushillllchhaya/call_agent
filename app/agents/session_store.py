"""
Session Store — Manages the ConversationSession database table.
Provides read/write helpers used by LangGraph nodes and CRM tools.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.models import ConversationSession

logger = logging.getLogger("session_store")


class SessionStore:
    """
    Thin wrapper around the ConversationSession SQLAlchemy model.
    All agent nodes use this to read/persist call state.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Create ─────────────────────────────────────────────────────────────────

    def create_session(
        self,
        call_id: str,
        phone_number: str,
        is_registered: bool = False,
        agent_used: str = "supervisor",
    ) -> ConversationSession:
        """Create a new session row at the start of a call."""
        session = ConversationSession(
            call_id=call_id,
            phone_number=phone_number,
            is_registered=is_registered,
            agent_used=agent_used,
            stage="greeting",
            history=json.dumps([]),
            customer_info=json.dumps({}),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        logger.info(f"[SessionStore] Created session {call_id} for {phone_number}")
        return session

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_session(self, call_id: str) -> Optional[dict]:
        """Return the session as a plain dict, or None if not found."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if not row:
            return None
        return {
            "call_id":       row.call_id,
            "phone_number":  row.phone_number,
            "is_registered": row.is_registered,
            "agent_used":    row.agent_used,
            "stage":         row.stage,
            "history":       json.loads(row.history or "[]"),
            "customer_info": json.loads(row.customer_info or "{}"),
            "created_at":    row.created_at,
            "updated_at":    row.updated_at,
        }

    def get_or_create(self, call_id: str, phone_number: str) -> dict:
        """Get existing session or create a new one."""
        existing = self.get_session(call_id)
        if existing:
            return existing
        session = self.create_session(call_id, phone_number)
        return self.get_session(call_id)

    # ── Update ─────────────────────────────────────────────────────────────────

    def update_stage(self, call_id: str, stage: str):
        """Update the conversation stage for a session."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            row.stage = stage
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def update_agent(self, call_id: str, agent: str):
        """Track which agent is currently handling the call."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            row.agent_used = agent
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def update_registration(self, call_id: str, is_registered: bool):
        """Update registration flag once lookup is complete."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            row.is_registered = is_registered
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def update_customer_info(self, call_id: str, info: dict):
        """Merge new customer info fields into the stored JSON dict."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            existing = json.loads(row.customer_info or "{}")
            existing.update({k: v for k, v in info.items() if v})
            row.customer_info = json.dumps(existing, ensure_ascii=False)
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def append_message(self, call_id: str, role: str, content: str):
        """
        Append a single message (role: 'human' | 'ai' | 'tool') to the
        session's history JSON array.
        """
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            history = json.loads(row.history or "[]")
            history.append({
                "role":      role,
                "content":   content,
                "timestamp": datetime.utcnow().isoformat(),
            })
            # Keep last 40 messages to prevent unbounded growth
            if len(history) > 40:
                history = history[-40:]
            row.history = json.dumps(history, ensure_ascii=False)
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def save_full_history(self, call_id: str, messages: list):
        """Replace entire history (used by LangGraph checkpointer sync)."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            row.history = json.dumps(messages, ensure_ascii=False, default=str)
            row.updated_at = datetime.utcnow()
            self.db.commit()

    # ── Delete ─────────────────────────────────────────────────────────────────

    def close_session(self, call_id: str, final_stage: str = "completed"):
        """Mark the session as closed when the call ends."""
        row = self.db.query(ConversationSession).filter(
            ConversationSession.call_id == call_id
        ).first()
        if row:
            row.stage = final_stage
            row.updated_at = datetime.utcnow()
            self.db.commit()
            logger.info(f"[SessionStore] Session {call_id} closed at stage: {final_stage}")

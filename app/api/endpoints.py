"""
API Endpoints — REST endpoints for the BSB Voice Agent system.
The old Orchestrator has been replaced by MultiAgentOrchestrator.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from pydantic import BaseModel

router = APIRouter()


class VoiceInput(BaseModel):
    call_id: str
    message: str
    phone_number: str = ""


class VoiceResponse(BaseModel):
    response: str
    should_end: bool = False


@router.post("/chat", response_model=VoiceResponse)
def handle_voice_chat(input_data: VoiceInput, db: Session = Depends(get_db)):
    """REST fallback endpoint — normally calls come through /ws/tata-audio."""
    from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator(db)
    try:
        text, end = orch.process_message(
            call_id=input_data.call_id,
            user_text=input_data.message,
            phone_number=input_data.phone_number,
        )
        return VoiceResponse(response=text, should_end=end)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    """List all conversation sessions for the dashboard."""
    from app.core.models import ConversationSession
    import json
    rows = db.query(ConversationSession).order_by(
        ConversationSession.updated_at.desc()
    ).limit(50).all()
    return [
        {
            "call_id":       r.call_id,
            "phone_number":  r.phone_number,
            "agent_used":    r.agent_used,
            "stage":         r.stage,
            "is_registered": r.is_registered,
            "created_at":    str(r.created_at),
            "updated_at":    str(r.updated_at),
            "customer_info": json.loads(r.customer_info or "{}"),
        }
        for r in rows
    ]

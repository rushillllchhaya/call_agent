from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
import json

from app.core.database import get_db
from app.core.models import ConversationSession, Lead

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Get high-level statistics for the dashboard."""
    total_calls = db.query(func.count(ConversationSession.id)).scalar() or 0
    total_leads = db.query(func.count(Lead.id)).scalar() or 0
    completed_calls = db.query(func.count(ConversationSession.id)).filter(
        ConversationSession.stage == 'completed'
    ).scalar() or 0

    return {
        "total_calls": total_calls,
        "completed_calls": completed_calls,
        "total_leads": total_leads,
        "active_calls": total_calls - completed_calls
    }

@router.get("/intents")
def get_intent_distribution(db: Session = Depends(get_db)):
    """Get call intent distribution for charting."""
    # Count calls by the last agent used as a proxy for intent
    stats = db.query(
        ConversationSession.agent_used, 
        func.count(ConversationSession.id)
    ).group_by(ConversationSession.agent_used).all()
    
    return {agent: count for agent, count in stats}

@router.get("/calls")
def get_recent_calls(
    limit: int = 50, 
    phone: str = None, 
    agent: str = None, 
    db: Session = Depends(get_db)
):
    """Get recent call history with optional filtering."""
    query = db.query(ConversationSession)
    
    if phone:
        query = query.filter(ConversationSession.phone_number.contains(phone))
    if agent and agent != "all":
        query = query.filter(ConversationSession.agent_used == agent)
        
    calls = query.order_by(
        desc(ConversationSession.created_at)
    ).limit(limit).all()

    results = []
    for call in calls:
        # Parse JSON info carefully
        try:
            info = json.loads(call.customer_info) if call.customer_info else {}
        except Exception:
            info = {}

        try:
            history = json.loads(call.history) if call.history else []
        except Exception:
            history = []

        duration = 0
        if call.updated_at and call.created_at:
            duration = int((call.updated_at - call.created_at).total_seconds())

        results.append({
            "id": call.id,
            "call_id": call.call_id,
            "phone_number": call.phone_number,
            "is_registered": call.is_registered,
            "agent_used": call.agent_used,
            "stage": call.stage,
            "duration": duration,
            "created_at": call.created_at.isoformat() if call.created_at else None,
            "customer_info": info,
            "message_count": len(history)
        })

    return results

@router.get("/calls/{call_id}/transcript")
def get_call_transcript(call_id: str, db: Session = Depends(get_db)):
    """Get the full transcript of a specific call."""
    call = db.query(ConversationSession).filter(
        ConversationSession.call_id == call_id
    ).first()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    try:
        history = json.loads(call.history) if call.history else []
    except Exception:
        history = []

    return {
        "call_id": call.call_id,
        "history": history
    }

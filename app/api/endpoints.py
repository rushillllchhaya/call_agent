from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.orchestrator import Orchestrator
from pydantic import BaseModel

router = APIRouter()

class VoiceInput(BaseModel):
    user_id: int
    message: str # Transcribed text from STT
    conversation_id: int = None

class VoiceResponse(BaseModel):
    response: str # Text to be sent to TTS
    state_updates: dict = None

@router.post("/chat", response_model=VoiceResponse)
def handle_voice_chat(input_data: VoiceInput, db: Session = Depends(get_db)):
    print(f"DEBUG: API Endpoint received request: {input_data}")
    orchestrator = Orchestrator(db)
    
    try:
        response_text = orchestrator.process_message(
            user_id=input_data.user_id,
            message_text=input_data.message,
            conversation_id=input_data.conversation_id
        )
        return VoiceResponse(response=response_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

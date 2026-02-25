from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
import logging
import json

# Setup specific logger for webhooks
logger = logging.getLogger("webhook")
logger.setLevel(logging.INFO)

# Add file handler
fh = logging.FileHandler("webhook_debug.log")
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

router = APIRouter()

@router.post("/incoming")
async def handle_incoming_call(request: Request, db: Session = Depends(get_db)):
    """
    Handle incoming webhooks from Tata Tele Smartflo.
    Payload Format (from User Screenshot):
    {
      "uuid": "$uuid",
      "call_id": "$call_id",
      "call_to_number": "$call_to_number",
      "caller_id_number": "$caller_id_number",
      "start_stamp": "$start_stamp",
      "billing_circle": "$billing_circle"
    }
    """
    try:
        # 1. Parse JSON
        body = await request.json()
        
        # Log to file for debugging
        logger.info(f"TATA REQUEST BODY: {body}")
        
        call_id = body.get("call_id")
        caller_id = body.get("caller_id_number")
        
        if not caller_id:
            logger.warning("Missing caller_id_number in payload")
            return {"status": "error", "message": "Missing caller_id_number"}
            
        # 2. Start/Resume Conversation via Orchestrator
        from app.services.orchestrator import Orchestrator
        from app.services.tts_service import TTSService
        
        orchestrator = Orchestrator(db)
        tts = TTSService()
        
        # For prototype, we map phone number to User ID
        # In real app, we'd look up User by phone number
        user_id = 999 # Placeholder for unknown caller
        
        # Check if we have a user with this phone
        # user = db.query(User).filter(User.phone == caller_id).first()
        # if user: user_id = user.id
        
        
        # 3. Generate Initial Greeting or Process Input?
        # Since this is "Start of Call" (Webhook), valid response is to play a greeting.
        
        greeting_text = "Hello! Welcome to BSB Polymer Trading. How can I help you today?"
        
        # We process a "dummy" start message to initialize state if needed, 
        # or just let the greeting be the first thing.
        # But we want to capture the lead phone number immediately.
        # We can call process_message with "Hi" to trigger user creation/lookup and lead init.
        
        try:
            # Initialize/Update User & Lead
            # Fixed: process_message signature accepts phone_number parameter
            # process_message returns a tuple: (ai_response, should_end_call)
            ai_response, _ = orchestrator.process_message(
                user_id=user_id,
                message_text="Hi",
                phone_number=caller_id
            )
            # Use AI response if it's better than hardcoded, or stick to standard greeting
            if ai_response and "trouble" not in ai_response.lower():
                greeting_text = ai_response
        except Exception as e:
            logger.error(f"Error initializing chat: {e}", exc_info=True)
            # Continue with default greeting even if orchestrator fails
        
        logger.info(f"AI Response Text: {greeting_text}")
        
        # 4. Convert text to speech using Deepgram TTS
        try:
            # Get the base URL from the request (ngrok URL)
            base_url = f"{request.url.scheme}://{request.url.netloc}"
            audio_url = tts.get_audio_url(greeting_text, base_url)
            
            if audio_url:
                logger.info(f"Generated TTS Audio: {audio_url}")
                # Return audio URL for Tata Smartflo to play
                return {
                    "response_text": greeting_text,
                    "audio_url": audio_url,
                    "action": "continue"
                }
            else:
                logger.warning("TTS generation failed, returning text-only response")
                # Fallback to TTS on Tata's side
                return {
                    "response_text": greeting_text,
                    "action": "continue"
                }
        except Exception as e:
            logger.error(f"TTS Error: {e}", exc_info=True)
            # Fallback to text response
            return {
                "response_text": greeting_text,
                "action": "continue"
            }
        
    except Exception as e:
        logger.error(f"[ERROR] Webhook processing failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

from fastapi import Response

@router.post("/twilio")
async def handle_twilio_call(request: Request, db: Session = Depends(get_db)):
    """
    Handle incoming calls from Twilio.
    Returns TwiML (XML) to control the call.
    """
    try:
        form_data = await request.form()
        logger.info(f"TWILIO REQUEST: {dict(form_data)}")
        
        call_sid = form_data.get("CallSid")
        caller = form_data.get("From")
        speech_result = form_data.get("SpeechResult") # Input from user
        
        # 1. Initial Greeting (if no speech yet)
        if not speech_result:
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Say>Hello! Welcome to BSB Polymer Trading. How can I help you today?</Say>
                <Gather input="speech" action="/webhook/twilio" timeout="5" speechTimeout="auto">
                </Gather>
            </Response>
            """
            return Response(content=twiml, media_type="application/xml")
            
        # 2. Process User IO with AI Agent
        from app.services.orchestrator import Orchestrator
        orchestrator = Orchestrator(db)
        
        # We need a numeric ID for the user. For prototype, we hash the phone number or use ID 1.
        # In real app: lookup user by phone number.
        user_id = 1 
        
        ai_response = orchestrator.process_message(user_id, speech_result)
        
        # 3. Respond with AI text
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say>{ai_response}</Say>
            <Gather input="speech" action="/webhook/twilio" timeout="5" speechTimeout="auto">
            </Gather>
        </Response>
        """
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.error(f"Twilio Error: {e}")
        return Response(content="<Response><Say>Sorry, an error occurred.</Say></Response>", media_type="application/xml")

@router.get("/incoming")
async def handle_incoming_call_get(request: Request):
    """
    Handle GET requests if Tata Tele uses GET for initial checks.
    """
    query_params = dict(request.query_params)
    print(f"\n[WEBHOOK GET DEBUG] Query Params: {query_params}\n")
    return {"status": "success"}

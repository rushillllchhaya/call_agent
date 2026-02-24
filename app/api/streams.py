from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.core.database import get_db, SessionLocal
from app.services.sarvam_service import SarvamService
from app.services.orchestrator import Orchestrator
from app.services.ai_agent import stream_ai_response_sentences
from app.core.models import Lead
from app.services.state import ConversationState


import logging
import json
import base64
import asyncio
import sys
import codecs
import audioop
import aiohttp
import os
import re
from typing import Deque, Optional
from enum import Enum

class CallStatus(Enum):
    LISTENING = 1
    THINKING = 2
    SPEAKING = 3

# Setup specific logger for streams
logger = logging.getLogger("stream")
logger.setLevel(logging.INFO)

# Add file handler
fh = logging.FileHandler("stream_debug.log", encoding='utf-8', errors='replace')
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

# also log to console with UTF-8 support
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# Attach handlers to sarvam_service logger as well
sarvam_logger = logging.getLogger("sarvam_service")
sarvam_logger.setLevel(logging.INFO)
sarvam_logger.addHandler(fh)
sarvam_logger.addHandler(ch)

router = APIRouter()


from typing import Deque
from collections import deque

@router.websocket("/tata-audio")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time bidirectional voice streaming endpoint.
    Receives audio from Tata → Sarvam STT → AI → Sarvam TTS → sends audio back
    """
    await websocket.accept()
    logger.info("WebSocket Connected: /tata-audio")
    
    # Initialize services
    voice_service = SarvamService()
    loop = asyncio.get_running_loop()
    

    def reset_state_sync(user_id: int):
        with SessionLocal() as db:
            orch = Orchestrator(db)
            orch.reset_state(user_id)

    # User ID for this call (will be resolved from Caller ID)
    user_id = 999 
    caller_phone = None
    
    # Buffer for accumulating audio chunks for VAD
    # We maintain a 'silence_buffer' (pre-roll) and 'audio_buffer' (active speech)
    audio_buffer = bytearray()
    silence_buffer: Deque[bytes] = deque(maxlen=10) # ~600ms of pre-roll context (assuming 60ms chunks)
    
    # VAD Parameters
    # Dynamic VAD: Load from env or fallback to 500
    SILENCE_THRESHOLD_MS = int(os.getenv("CALL_SILENCE_MS", 500))
    VOICE_THRESHOLD = int(os.getenv("VOICE_THRESHOLD", 150))
    MAX_BUFFER_SIZE = 1024 * 500 # 500KB Safety limit
    
    last_voice_time = 0
    is_speech_active = False # True if we are currently recording a "sentence"
    
    # Track stream ID from Tata
    stream_sid = None
    
    # Track if bot is currently speaking (to avoid processing echo)
    call_state = CallStatus.LISTENING
    current_language = "hi-IN"   # tracks caller's detected language across turns
    language_locked = False

    interruption_event = asyncio.Event()
    processing_task = None
    last_tts_start_time = 0

    async def process_speech_buffer():
        nonlocal audio_buffer, is_speech_active, current_language, call_state, language_locked
        
        # If already thinking, ignore new speech
        if call_state == CallStatus.THINKING:
            logger.info("Ignoring speech buffer: already in THINKING state.")
            audio_buffer.clear()
            return

        interruption_event.clear() # Reset for this new turn
        
        if len(audio_buffer) == 0:
            return

        logger.info(f"VAD Triggered: Processing {len(audio_buffer)} bytes")
        
        audio_data = bytes(audio_buffer)
        audio_buffer.clear()
        is_speech_active = False

        # ── STT (REST-Primary) ────────────────────────────────────────────────
        logger.info(f"Transcribing via REST using: {current_language}")
        transcript, detected_lang = await voice_service.transcribe_audio(audio_data, current_language)
        
        # ── Language Lockdown (HI, EN, GU only) ──────────────────────────────
        if detected_lang and not language_locked:
            VALID_TRIO = ["hi-IN", "gu-IN", "en-IN"]
            
            # If the detected language is completely outside our trio, ignore it
            if detected_lang in VALID_TRIO:
                logger.info(f"Language Locked (Supported detected): {detected_lang}")
                current_language = detected_lang
                language_locked = True
        
        if not (transcript and transcript.strip()):
            logger.info("No transcript (silence/noise)")
            return

        # ── Noise Guard ───────────────────────────────────────────────────────
        from app.services.language_handler import LanguageHandler
        try:
            english_text = LanguageHandler.normalize_text(transcript)
        except Exception:
            english_text = transcript
        
        stripped_check = english_text.strip().lower().rstrip(".")
        words_check    = stripped_check.split()
        
        # Relaxed Noise Guard: allow short but valid conversational markers
        is_noise = (
            len(stripped_check) <= 1  # single char is usually noise
            or (len(words_check) >= 2 and len(set(words_check)) == 1) # repetitive words
        )
        
        # Whitelist common short responses
        if stripped_check in ["hi", "hello", "हाँ", "ji", "जी", "नही", "ok", "okay", "sure", "ਹਾਂਜੀ"]:
            is_noise = False

        # ── Script Guard ──────────────────────────────────────────────────────
        # Reject characters from scripts outside our target trio (EN, HI, GU).
        # This catches non-semantic noise that STT transcribes into Gurmukhi, Tamil, Odia, etc.
        # Allowed: Latin(EN), Devanagari(HI), Gujarati(GU).
        # Junk: \u0980-\u09FF (BE), \u0A00-\u0A7F (Gurmukhi), \u0B00-\u0D7F (OR, TA, TE, KA, MA)
        JUNK_SCRIPT_RE = re.compile(r'[\u0980-\u09FF\u0A00-\u0A7F\u0B00-\u0D7F]')
        if JUNK_SCRIPT_RE.search(transcript):
            logger.info(f"[STREAM] Junk script detected in transcript: '{transcript}'. Flagging as noise.")
            is_noise = True

        # STT HALLUCINATION BLACKLIST: Filter out common noise-triggered nonsense
        HALLUCINATION_BLACKLIST = [
            "the market has been moving",
            "strong movement in the market",
            "line is one sir",
            "let's have a drink",
            "two other people sir",
            "what kind of boy",
            "ok sir ok ok i will tell you"
        ]
        
        found_bad = False
        text_to_check = str(stripped_check)
        for bad_phrase in HALLUCINATION_BLACKLIST:
            if bad_phrase in text_to_check:
                found_bad = True
                break
        
        if found_bad:
            logger.warning(f"[STREAM] Blacklisted hallucination detected: '{transcript}'")
            return
            
        if is_noise:
            logger.info(f"[STREAM] Skipping noise transcript: '{transcript}' (normalized: '{stripped_check}')")
            return

        logger.info(f"User said [{current_language}]: {transcript}")
        call_state = CallStatus.THINKING

        try:
            # ── Load state & build orchestrator context (in executor) ─────────
            def _load_context():
                with SessionLocal() as db:
                    orch = Orchestrator(db)
                    state_obj = orch._load_state(user_id)
                    lang_label = {
                        "hi-IN": "Hindi (Hinglish is fine, use Devanagari where natural)",
                        "gu-IN": "Gujarati (mix with Hindi/English naturally if needed)",
                        "en-IN": "English (you may sprinkle Hindi courtesy words like 'Ji', 'Theek hai')",
                    }.get(state_obj.detected_language, "Hindi")
                    lang_instruction = (
                        f"LANGUAGE: The caller is currently speaking {lang_label}. "
                        f"Respond primarily in {lang_label}. "
                        "CRITICAL: Always fill the 'ai_response' key FIRST with your spoken text. NEVER leave it empty."
                    )
                    stage_instr = orch._stage_instruction(state_obj)
                    system_instruction_inner = (lang_instruction + "\n" + stage_instr) if stage_instr else lang_instruction
                    from app.services.db_context import build_catalog_context
                    catalog_inner = build_catalog_context(db, polymer=state_obj.customer.polymer_needed)
                    # stage advancement is now handled inside orchestrator.py:process_message
                    # so we don't hardcode it here anymore.
                    return state_obj, system_instruction_inner, catalog_inner

            state, system_instruction, catalog = await asyncio.get_running_loop().run_in_executor(None, _load_context)

            # ── Audio playback queue (in-order) ───────────────────────────────
            # Producer fills it with (audio_bytes) as TTS completes per sentence.
            # Consumer sends them to Tata in order.
            audio_queue: asyncio.Queue = asyncio.Queue()
            SENTINEL = None  # signals end of stream

            sentence_id_counter = 0
            all_sentences = []   # collect for full ai_response reconstruction
            final_result = {}    # will hold the parsed JSON from LLM

            async def _producer():
                """Stream sentences from LLM → fire TTS per sentence → enqueue audio."""
                nonlocal final_result, sentence_id_counter
                async for sentence, result_dict in stream_ai_response_sentences(
                    transcript, state, system_instruction, catalog
                ):
                    if interruption_event.is_set():
                        logger.info("[STREAM] Producer interrupted by user. Stopping.")
                        break

                    if result_dict is not None:
                        # Last yield from generator — contains parsed JSON
                        final_result = result_dict
                        break

                    if not sentence:
                        continue

                    sentence_id_counter += 1
                    s_id = sentence_id_counter
                    # Safety: skip sentences that are just JSON symbols or whitespace
                    if not re.search(r'[a-zA-Z\u0900-\u097F\u0A80-\u0AFF]', sentence):
                        logger.warning(f"[STREAM] Skipping non-speech sentence fragment: {sentence}")
                        continue

                    all_sentences.append(sentence)
                    logger.info(f"[STREAM] Queueing Sentence #{s_id}: {sentence}")

                    async def _run_tts(text_chunk):
                        try:
                            return await voice_service.text_to_speech(text_chunk, language_code=current_language)
                        except Exception as tts_err:
                            logger.error(f"TTS error for chunk '{text_chunk}': {tts_err}")
                            return b""

                    # Fire TTS for this sentence immediately (Conveyor Belt)
                    if sentence_id_counter > 4:
                        logger.info("Conversational Rhythm: Limiting response to 4 sentences.")
                        break
                        
                    tts_task = asyncio.create_task(_run_tts(sentence))
                    audio_queue.put_nowait((s_id, tts_task))

                audio_queue.put_nowait(SENTINEL)  # signal end

            total_audio_bytes = 0  # track total for final sleep

            async def _consumer():
                """Send all audio chunks to Tata immediately back-to-back."""
                nonlocal total_audio_bytes
                while True:
                    if interruption_event.is_set():
                        # Clear remaining queue items
                        while not audio_queue.empty():
                            try:
                                audio_queue.get_nowait()
                            except:
                                break
                        logger.info("[STREAM] Consumer interrupted. Flushing audio queue.")
                        break

                    item = await audio_queue.get()
                    if item is SENTINEL:
                        break
                    
                    s_id, tts_task = item
                    # Await the TTS task
                    audio_chunk = await tts_task
                    
                    if interruption_event.is_set():
                        # Second check after await
                        break

                    if audio_chunk:
                        total_audio_bytes += len(audio_chunk)
                        chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
                        msg = json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": chunk_b64}
                        })
                        try:
                            await websocket.send_text(msg)
                            logger.info(f"[STREAM] Playing Sentence #{s_id} ({len(audio_chunk)} bytes)")
                        except Exception as send_err:
                            logger.error(f"Failed to send audio chunk: {send_err}")

            # Run producer and consumer concurrently
            await asyncio.gather(_producer(), _consumer())

            # ── Reconstruct full ai_response & update state (in executor) ─────
            full_ai_response = " ".join(all_sentences) or final_result.get("ai_response", "")
            logger.info(f"[STREAM] Full AI Response Reconstructed: {full_ai_response}")
            if final_result:
                logger.info(f"[STREAM] Final AI Result Dict: {json.dumps(final_result, ensure_ascii=False)}")
            
            # MANDATORY FALLBACK: If no sentences were generated (timeout/refusal), Mohini must speak.
            if not all_sentences:
                fallback_msg = "Ji, thoda repeat karein please? Network slow lag raha hai."
                logger.warning(f"[STREAM] LLM returned empty response. Triggering fallback: '{fallback_msg}'")
                audio_chunk = await voice_service.text_to_speech(fallback_msg, language_code=current_language)
                if audio_chunk:
                    chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
                    await websocket.send_text(json.dumps({
                        "event": "media", "streamSid": stream_sid,
                        "media": {"payload": chunk_b64}
                    }))
                full_ai_response = fallback_msg

            if not final_result:
                final_result = {"ai_response": full_ai_response}
            elif not final_result.get("ai_response"):
                final_result["ai_response"] = full_ai_response

            def _update_state():
                with SessionLocal() as db:
                    orch = Orchestrator(db)
                    # Re-use orchestrator's state-update logic by calling process_message
                    # BUT we already have the AI result — so we patch it directly.
                    state_fresh = orch._load_state(user_id)
                    from app.services.language_handler import LanguageHandler
                    try:
                        english_text = LanguageHandler.normalize_text(transcript)
                    except Exception:
                        english_text = transcript
                    # Apply extracted customer fields
                    c = state_fresh.customer
                    if final_result.get("customer_name")   and not c.name:
                        c.name             = str(final_result["customer_name"])
                    if final_result.get("customer_type")   and not c.customer_type:
                        raw_t = str(final_result["customer_type"]).lower()
                        c.customer_type = "manufacturer" if "manuf" in raw_t else "trader" if "trad" in raw_t else raw_t
                    if final_result.get("product_made")    and not c.product_made:
                        c.product_made     = str(final_result["product_made"])
                    if final_result.get("polymer_needed")  and not c.polymer_needed:
                        c.polymer_needed   = str(final_result["polymer_needed"])
                    if final_result.get("grade_needed")    and not c.grade_needed:
                        c.grade_needed     = str(final_result["grade_needed"])
                    if final_result.get("brand_preferred") and not c.brand_preferred:
                        c.brand_preferred  = str(final_result["brand_preferred"])
                    if final_result.get("factory_location") and not c.factory_location:
                        c.factory_location = str(final_result["factory_location"])

                    # Brand → Grade auto-resolution
                    from app.services.brand_resolver import resolve_grade
                    if c.polymer_needed and c.brand_preferred and not c.grade_needed:
                        resolved = resolve_grade(db, c.polymer_needed, c.brand_preferred)
                        if resolved:
                            c.grade_needed = resolved

                    # Role
                    if c.customer_type and not state_fresh.role:
                        state_fresh.role = "buyer" if c.customer_type == "manufacturer" else "seller"

                    state_fresh.customer = c

                    # Lead upsert
                    if c.name or c.factory_location:
                        orch._upsert_lead(state_fresh, caller_phone)

                    # Advance stage
                    from app.services.state import ConversationStage
                    from app.services.orchestrator import _next_stage
                    if final_result.get("advance_stage") and state_fresh.stage != ConversationStage.COMPLETED:
                        if state_fresh.stage == ConversationStage.QUALIFICATION:
                            if all([c.product_made, c.polymer_needed, c.grade_needed, c.factory_location, c.quantity_needed]):
                                state_fresh.stage = _next_stage(state_fresh.stage)
                        else:
                            state_fresh.stage = _next_stage(state_fresh.stage)

                    if state_fresh.stage == ConversationStage.COMPLETED or final_result.get("call_completed"):
                        state_fresh.should_end_call = True

                    state_fresh.add_turn(english_text, full_ai_response)
                    orch._save_state(user_id, state_fresh)
                    return state_fresh.should_end_call

            should_end_call = await loop.run_in_executor(None, _update_state)

            # Optional small trailing gap
            await asyncio.sleep(0.4)

            if should_end_call:
                logger.info("Call completed — agent ending call.")
                await websocket.send_text(json.dumps({"event": "stop", "streamSid": stream_sid}))
                await asyncio.sleep(0.5)
                await websocket.close()

        except Exception as e:
            logger.error(f"AI Streaming Pipeline Error: {e}", exc_info=True)
        finally:
            call_state = CallStatus.LISTENING

    # Tracks consecutive spikes for Barge-In persistence
    barge_in_persistence = 0

    try:
        loop = asyncio.get_running_loop()
        last_voice_time = loop.time()

        while True:
            try:
                # Receive with timeout to allow silence check loop
                message = await asyncio.wait_for(websocket.receive(), timeout=0.1)
                
                if "text" in message:
                    data = message["text"]
                    json_data = json.loads(data)
                    event_type = json_data.get("event")
                    
                    if event_type == "start":
                        stream_sid = json_data.get("start", {}).get("streamSid")
                        metadata = json_data.get("start", {})
                        caller_phone = metadata.get("from", "Unknown")
                        logger.info(f"Stream started: {json_data}")
                        logger.info(f"Caller Phone: {caller_phone}")

                        # Find Lead to set User ID (Sync DB call in thread)
                        if caller_phone and caller_phone != "Unknown":
                            def get_lead_info(phone):
                                """Returns (lead_id, known_name) for the caller, or (999, None) if new."""
                                with SessionLocal() as db:
                                    lead = db.query(Lead).filter(Lead.mobile_no == phone).order_by(Lead.id.desc()).first()
                                    if lead:
                                        return lead.id, lead.customer_name
                                    return 999, None

                            user_id, known_caller_name = await loop.run_in_executor(None, get_lead_info, caller_phone)
                            logger.info(f"Mapped to User ID: {user_id} | Known name: {known_caller_name}")
                        else:
                            known_caller_name = None

                        # Reset state for this call
                        await loop.run_in_executor(None, reset_state_sync, user_id)
                        logger.info(f"Reset state for User ID: {user_id}")

                        # Pre-populate known caller name so AI skips name-collection
                        if known_caller_name:
                            def inject_name(uid, name):
                                with SessionLocal() as db:
                                    orch = Orchestrator(db)
                                    state = orch._load_state(uid)
                                    state.customer.name = name
                                    orch._save_state(uid, state)
                            await loop.run_in_executor(None, inject_name, user_id, known_caller_name)
                            logger.info(f"Pre-populated caller name: {known_caller_name}")
                        
                        # Initial Greeting
                        if known_caller_name:
                            greeting_text = f"Ji Namaste {known_caller_name} ji! Main Mohini hun, BSB Polymer se baat kar rahi hun. Batayein, main aapki kya help kar sakti hun?"
                        else:
                            greeting_text = "Ji Namaste! Main Mohini hun, BSB Polymer se baat kar rahi hun. Kya main aapka shubh naam jaan sakti hun?"
                        call_state = CallStatus.SPEAKING
                        last_tts_start_time = asyncio.get_running_loop().time()
                        greeting_audio = await voice_service.text_to_speech(greeting_text)
                        if greeting_audio:
                            greeting_b64 = base64.b64encode(greeting_audio).decode('utf-8')
                            await websocket.send_text(json.dumps({
                                "event": "media", "streamSid": stream_sid, "media": {"payload": greeting_b64}
                            }))
                        call_state = CallStatus.LISTENING

                    elif event_type == "media":
                        payload = json_data.get("media", {}).get("payload")
                        if payload and call_state == CallStatus.SPEAKING:
                            # BARGE-IN CHECK: Is user talking while we are?
                            # Cooldown: Ignore barge-in for first 400ms
                            if asyncio.get_running_loop().time() - last_tts_start_time < 0.4:
                                continue

                            chunk = base64.b64decode(payload)
                            pcm_chunk = audioop.ulaw2lin(chunk, 2)
                            rms = audioop.rms(pcm_chunk, 2)
                            
                            # Production Barge-In Threshold: 1200 RMS, 3 consecutive chunks
                            if rms > 1200: 
                                barge_in_persistence += 1
                                if barge_in_persistence >= 3:
                                    logger.info(f"BARGE-IN DETECTED (RMS: {rms}, persistence: {barge_in_persistence})! Interrupting bot.")
                                    interruption_event.set()
                                    call_state = CallStatus.LISTENING
                                    barge_in_persistence = 0
                            else:
                                barge_in_persistence = 0
                            continue

                        if call_state != CallStatus.LISTENING:
                            continue
                        
                        payload = json_data.get("media", {}).get("payload")
                        if payload:
                            chunk = base64.b64decode(payload)
                            
                            try:
                                # VAD Logic
                                pcm_chunk = audioop.ulaw2lin(chunk, 2)
                                rms = audioop.rms(pcm_chunk, 2)
                                
                                # ── Local VAD ──
                                if rms > VOICE_THRESHOLD:
                                    # Speech Detected
                                    last_voice_time = asyncio.get_running_loop().time()
                                    
                                    if not is_speech_active:
                                        # Start of new phrase: flush silence buffer as pre-roll
                                        is_speech_active = True
                                        logger.info(f"Voice started (RMS: {rms})")
                                        if silence_buffer:
                                            for s_chunk in silence_buffer:
                                                audio_buffer.extend(s_chunk)
                                            silence_buffer.clear()
                                    
                                    audio_buffer.extend(chunk)
                                
                                else:
                                    # Silence/Noise
                                    if is_speech_active:
                                        # We were in speech, now entering silence
                                        if asyncio.get_running_loop().time() - last_voice_time > SILENCE_THRESHOLD_MS / 1000:
                                            # End of phrase detected
                                            is_speech_active = False
                                            logger.info(f"VAD Triggered: Processing {len(audio_buffer)} bytes")
                                            processing_task = asyncio.create_task(process_speech_buffer())
                                    else:
                                        # Passive silence: keep short buffer for pre-roll
                                        silence_buffer.append(chunk)
                                        while len(silence_buffer) > 10: # ~400ms pre-roll
                                            silence_buffer.popleft()
                            
                            except Exception as e:
                                logger.error(f"Error processing audio chunk: {e}")

                    elif event_type == "stop":
                        logger.info("Stream stopped")
                        break
                        
            except asyncio.TimeoutError:
                # Local Silence Timeout Check (VAD Fallback)
                if is_speech_active:
                     duration_silence = asyncio.get_running_loop().time() - last_voice_time
                     if duration_silence > (SILENCE_THRESHOLD_MS / 1000.0):
                         # Silence exceeded threshold -> End of phrase
                         if processing_task is None or processing_task.done():
                             processing_task = asyncio.create_task(process_speech_buffer())
                continue
                
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except Exception as e:
                if "receive" in str(e) and "disconnect" in str(e):
                    break
                logger.error(f"Error in loop: {e}")
                
    except Exception as e:
        logger.error(f"Endpoint Error: {e}")
    finally:
        logger.info("Cleaned up resources")


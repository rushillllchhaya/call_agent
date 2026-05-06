from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.core.database import get_db, SessionLocal
from app.services.sarvam_service import SarvamService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.core.models import Lead
from app.services.dashboard import dashboard_manager


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
    Receives audio from Tata → Sarvam STT → Multi-Agent LangGraph → Sarvam TTS → sends audio back.
    """
    await websocket.accept()
    logger.info("WebSocket Connected: /tata-audio")

    # Initialize services
    voice_service = SarvamService()
    loop = asyncio.get_running_loop()

    # ── Multi-Agent Orchestrator (LangGraph) ──────────────────────────────────
    # One orchestrator per call; db session opened per-turn in thread executor.
    def _make_orchestrator():
        from app.core.database import SessionLocal
        db = SessionLocal()
        return MultiAgentOrchestrator(db), db

    multi_orch, _orch_db = await loop.run_in_executor(None, _make_orchestrator)

    # Unique call identifier — set from stream_sid when stream starts
    call_id: str = str(__import__("uuid").uuid4())
    caller_phone: str = ""
    
    # Buffer for accumulating audio chunks for VAD
    # We maintain a 'silence_buffer' (pre-roll) and 'audio_buffer' (active speech)
    audio_buffer = bytearray()
    silence_buffer: Deque[bytes] = deque(maxlen=10) # ~600ms of pre-roll context (assuming 60ms chunks)
    
    # VAD Parameters
    # TELECOM NOTE: Phone audio is heavily compressed — RMS is much lower than mic input.
    # Threshold 40 works for 8kHz mulaw telecom audio. Default 150 was designed for mic.
    SILENCE_THRESHOLD_MS = int(os.getenv("CALL_SILENCE_MS", 700))
    VOICE_THRESHOLD = int(os.getenv("VOICE_THRESHOLD", 40))   # was 150 — too high for telecom
    MAX_BUFFER_SIZE = 1024 * 500  # 500KB Safety limit
    MIN_AUDIO_BYTES = 3200  # ~400ms at 8kHz — anything shorter is noise
    vad_suppressed_until = 0  # timestamp until which VAD is muted (greeting/TTS echo)
    
    last_voice_time = 0
    is_speech_active = False # True if we are currently recording a "sentence"
    
    # Track stream ID from Tata
    stream_sid = None
    
    # Track if bot is currently speaking (to avoid processing echo)
    call_state = CallStatus.LISTENING
    current_language = "hi-IN"   # tracks caller's detected language across turns
    # VALID_TRIO: STT will ONLY ever be called with one of these.
    # This means the STT engine literally cannot output Tamil, Gurmukhi, etc.
    VALID_TRIO = ["hi-IN", "en-IN", "gu-IN"]

    interruption_event = asyncio.Event()
    processing_task = None
    last_tts_start_time = 0

    async def process_speech_buffer():
        nonlocal audio_buffer, is_speech_active, current_language, call_state
        
        # If already thinking, ignore new speech
        if call_state == CallStatus.THINKING:
            logger.info("Ignoring speech buffer: already in THINKING state.")
            audio_buffer.clear()
            return

        interruption_event.clear() # Reset for this new turn
        
        if len(audio_buffer) == 0:
            return

        # Reject very short buffers — almost always ambient noise
        if len(audio_buffer) < MIN_AUDIO_BYTES:
            logger.info(f"Buffer too short ({len(audio_buffer)} bytes < {MIN_AUDIO_BYTES}). Skipping noise.")
            audio_buffer.clear()
            is_speech_active = False
            return

        logger.info(f"VAD Triggered: Processing {len(audio_buffer)} bytes")
        
        audio_data = bytes(audio_buffer)
        audio_buffer.clear()
        is_speech_active = False

        # ── STT (REST-Primary) ────────────────────────────────────────────────
        # ALWAYS pass a language from the valid trio. Never let an unsupported lang propagate.
        stt_lang = current_language if current_language in VALID_TRIO else "hi-IN"
        logger.info(f"Transcribing via REST using: {stt_lang}")
        transcript, detected_lang = await voice_service.transcribe_audio(audio_data, stt_lang)
        
        # ── Language Adaptation (HI, EN, GU only) ────────────────────────────
        # STT service already clamps to valid trio, so detected_lang is always valid.
        if detected_lang and detected_lang in VALID_TRIO:
            if detected_lang != current_language:
                logger.info(f"Language switched: {current_language} → {detected_lang}")
                current_language = detected_lang
        
        if not (transcript and transcript.strip()):
            logger.info("No transcript (silence/noise)")
            return

        # ── Noise Guard ───────────────────────────────────────────────────────
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
        
        # Blacklist common noise-triggered single words
        NOISE_WORDS = {"cough", "hmm", "uh", "um", "ah", "huh", "oh", "mm", "umm", "ahem"}
        if stripped_check in NOISE_WORDS:
            is_noise = True

        # ── Script Guard ──────────────────────────────────────────────────────
        # If transcript contains non-target scripts (Bengali, Gurmukhi, Tamil, etc.),
        # it's likely a misdetection. Re-transcribe with hi-IN instead of dropping.
        JUNK_SCRIPT_RE = re.compile(r'[\u0980-\u09FF\u0A00-\u0A7F\u0B00-\u0D7F]')
        if JUNK_SCRIPT_RE.search(transcript):
            logger.warning(f"[STREAM] Junk script in transcript: '{transcript}'. Re-transcribing with hi-IN.")
            transcript, _ = await voice_service.transcribe_audio(audio_data, "hi-IN")
            if not (transcript and transcript.strip()):
                logger.info("[STREAM] Re-transcription returned empty. Dropping.")
                return
            logger.info(f"[STREAM] Re-transcribed as: '{transcript}'")
            english_text = transcript
            stripped_check = english_text.strip().lower().rstrip(".")
            words_check = stripped_check.split()
            # Re-check if still junk after hi-IN re-transcription
            if JUNK_SCRIPT_RE.search(transcript):
                logger.info(f"[STREAM] Still junk after re-transcription. Dropping.")
                return

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
        try:
            asyncio.create_task(dashboard_manager.broadcast({
                "type": "transcript",
                "speaker": "user",
                "text": transcript
            }))
        except Exception:
            pass

        call_state = CallStatus.THINKING

        try:
            # ── Multi-Agent LangGraph Pipeline ────────────────────────────────
            # Audio playback queue (in-order): sentences → TTS → Tata
            audio_queue: asyncio.Queue = asyncio.Queue()
            SENTINEL = None

            sentence_id_counter = 0
            all_sentences = []
            should_end_call = False

            async def _producer():
                """Stream sentences from multi-agent graph → TTS → audio queue."""
                nonlocal sentence_id_counter, should_end_call

                async for sentence, is_final in multi_orch.stream_response_sentences(
                    call_id, transcript, caller_phone, current_language
                ):
                    if is_final:
                        should_end_call = sentence if isinstance(sentence, bool) else is_final
                        break

                    if interruption_event.is_set():
                        logger.info("[STREAM] Producer interrupted by user. Stopping.")
                        break

                    if not sentence:
                        continue

                    sentence_id_counter += 1
                    s_id = sentence_id_counter

                    # Safety: skip non-speech fragments
                    if not re.search(r'[a-zA-Z\u0900-\u097F\u0A80-\u0AFF]', sentence):
                        logger.warning(f"[STREAM] Skipping non-speech fragment: {sentence}")
                        continue

                    # Conversational rhythm: cap at 4 sentences per turn
                    if sentence_id_counter > 4:
                        logger.info("[STREAM] Rhythm: Limiting to 4 sentences.")
                        break

                    all_sentences.append(sentence)
                    logger.info(f"[STREAM] Queueing Sentence #{s_id}: {sentence}")

                    try:
                        asyncio.create_task(dashboard_manager.broadcast({
                            "type": "transcript",
                            "speaker": "agent",
                            "text": sentence
                        }))
                    except Exception:
                        pass

                    async def _run_tts(text_chunk):
                        try:
                            return await voice_service.text_to_speech(text_chunk, language_code=current_language)
                        except Exception as tts_err:
                            logger.error(f"TTS error for chunk '{text_chunk}': {tts_err}")
                            return b""

                    tts_task = asyncio.create_task(_run_tts(sentence))
                    audio_queue.put_nowait((s_id, tts_task))

                audio_queue.put_nowait(SENTINEL)

            total_audio_bytes = 0

            async def _consumer():
                """Send audio chunks to Tata in order."""
                nonlocal total_audio_bytes
                while True:
                    if interruption_event.is_set() and not should_end_call:
                        while not audio_queue.empty():
                            try:
                                audio_queue.get_nowait()
                            except Exception:
                                break
                        logger.info("[STREAM] Consumer interrupted.")
                        break

                    item = await audio_queue.get()
                    if item is SENTINEL:
                        break

                    s_id, tts_task = item
                    audio_chunk = await tts_task

                    if interruption_event.is_set() and not should_end_call:
                        break

                    if audio_chunk:
                        total_audio_bytes += len(audio_chunk)
                        chunk_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                        msg = json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": chunk_b64},
                        })
                        try:
                            await websocket.send_text(msg)
                            logger.info(f"[STREAM] Playing Sentence #{s_id} ({len(audio_chunk)} bytes)")
                        except Exception as send_err:
                            logger.error(f"[STREAM] Failed to send audio: {send_err}")

            await asyncio.gather(_producer(), _consumer())

            # ── Fallback: if LangGraph returned nothing, speak a filler ────────
            full_ai_response = " ".join(all_sentences)
            if not full_ai_response:
                fallback_msg = "Ji, thoda repeat karein please? Network slow lag raha hai."
                logger.warning(f"[STREAM] Empty response. Fallback: '{fallback_msg}'")
                audio_chunk = await voice_service.text_to_speech(fallback_msg, language_code=current_language)
                if audio_chunk:
                    chunk_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                    await websocket.send_text(json.dumps({
                        "event": "media", "streamSid": stream_sid,
                        "media": {"payload": chunk_b64},
                    }))

            logger.info(f"[STREAM] Full Response: {full_ai_response[:120]}")

            # Wait for TTS audio to finish playing on Tata's side
            # mu-law 8kHz = 8000 bytes/sec, so duration ≈ bytes / 8000
            audio_play_secs = max(total_audio_bytes / 8000.0, 0.5)
            await asyncio.sleep(audio_play_secs + 0.5)  # audio duration + small buffer

            if should_end_call:
                logger.info(f"Call completed — waiting {audio_play_secs + 2:.1f}s for final audio to play.")
                # Extra grace period for the goodbye sentence to fully play
                await asyncio.sleep(2.0)
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
                        metadata   = json_data.get("start", {})
                        caller_phone = metadata.get("from", "")
                        call_id = stream_sid or call_id  # prefer Tata stream_sid as call_id
                        logger.info(f"Stream started: sid={stream_sid} phone={caller_phone}")

                        # Find Lead for legacy user_id mapping
                        known_caller_name = None
                        if caller_phone:
                            def get_lead_info(phone):
                                with SessionLocal() as db:
                                    lead = db.query(Lead).filter(Lead.mobile_no == phone).order_by(Lead.id.desc()).first()
                                    if lead:
                                        return lead.customer_name
                                    return None
                            known_caller_name = await loop.run_in_executor(
                                None, get_lead_info, caller_phone
                            )
                            logger.info(f"Known name: {known_caller_name}")

                        # ── Start multi-agent session ─────────────────────────
                        def _start_session():
                            return multi_orch.start_call(
                                call_id=call_id,
                                phone_number=caller_phone,
                                known_name=known_caller_name,
                            )
                        greeting_text = await loop.run_in_executor(None, _start_session)
                        logger.info(f"[MultiAgent] Session started: call_id={call_id}")

                        # ── Pre-fetch buyer details in parallel with TTS ──────
                        # This saves 5-12 seconds on the first user message by
                        # fetching buyer history WHILE the greeting plays.
                        buyer_details_task = None
                        if caller_phone:
                            from app.services.bsb_api_client import fetch_buyer_details
                            buyer_details_task = asyncio.create_task(
                                fetch_buyer_details(caller_phone)
                            )

                        call_state = CallStatus.SPEAKING
                        last_tts_start_time = asyncio.get_running_loop().time()
                        greeting_audio = await voice_service.text_to_speech(greeting_text)
                        if greeting_audio:
                            greeting_b64 = base64.b64encode(greeting_audio).decode('utf-8')
                            await websocket.send_text(json.dumps({
                                "event": "media", "streamSid": stream_sid, "media": {"payload": greeting_b64}
                            }))
                            logger.info(f"[GREETING] Sent greeting audio to Tata ({len(greeting_audio)} bytes mu-law). Text: '{greeting_text}'")
                            
                            # Suppress VAD for the duration of the greeting
                            # mu-law 8kHz = 8000 bytes/sec
                            greeting_duration = len(greeting_audio) / 8000.0
                            vad_suppressed_until = asyncio.get_running_loop().time() + greeting_duration + 0.5
                            logger.info(f"[GREETING] VAD suppressed for {greeting_duration + 0.5:.1f}s")
                            
                            try:
                                asyncio.create_task(dashboard_manager.broadcast({
                                    "type": "transcript",
                                    "speaker": "agent",
                                    "text": greeting_text
                                }))
                            except Exception:
                                pass
                        else:
                            logger.error("[GREETING] TTS returned empty audio for greeting! User will hear silence.")

                        # ── Collect pre-fetched buyer details ─────────────────
                        if buyer_details_task:
                            try:
                                buyer_details = await asyncio.wait_for(buyer_details_task, timeout=10.0)
                                multi_orch.set_prefetched_buyer_details(call_id, buyer_details)
                                logger.info(f"[PREFETCH] Buyer details cached: exists={buyer_details.get('exists')}")
                            except Exception as e:
                                logger.warning(f"[PREFETCH] Buyer details fetch failed: {e}")

                        call_state = CallStatus.LISTENING

                    elif event_type == "media":
                        payload = json_data.get("media", {}).get("payload")
                        if payload and call_state == CallStatus.SPEAKING:
                            # BARGE-IN CHECK: Is user talking while we are?
                            # Cooldown: Ignore barge-in for first 600ms (allow greeting to land)
                            if asyncio.get_running_loop().time() - last_tts_start_time < 0.6:
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
                        
                        # Suppress VAD during greeting/TTS echo playback
                        if asyncio.get_running_loop().time() < vad_suppressed_until:
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
                                        is_speech_active = True
                                        logger.info(f"Voice started (RMS: {rms}, threshold: {VOICE_THRESHOLD})")
                                        if silence_buffer:
                                            for s_chunk in silence_buffer:
                                                audio_buffer.extend(s_chunk)  # pre-roll (mulaw bytes)
                                            silence_buffer.clear()
                                    
                                    # Store mulaw bytes (not pcm) — sarvam_service converts them
                                    audio_buffer.extend(chunk)
                                else:
                                    # Log RMS occasionally to help tune threshold
                                    if len(audio_buffer) == 0:
                                        logger.debug(f"Silence RMS: {rms} (need >{VOICE_THRESHOLD} for speech)")
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


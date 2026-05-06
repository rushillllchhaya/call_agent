from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.core.database import get_db, SessionLocal
from app.services.sarvam_service import SarvamService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.core.models import Lead
from app.services.dashboard import dashboard_manager
from app.core.vad_manager import EnhancedVADManager, VADConfig, VADState
from app.core.pipeline import VoicePipeline, PipelineConfig

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

logger = logging.getLogger("stream")

router = APIRouter()

@router.websocket("/tata-audio")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time bidirectional voice streaming endpoint.
    Receives audio from Tata → VAD → Pipeline (STT → LLM → TTS) → sends audio back.
    """
    await websocket.accept()
    logger.info("WebSocket Connected: /tata-audio")

    # Initialize services
    voice_service = SarvamService()
    loop = asyncio.get_running_loop()

    # Create Multi-Agent Orchestrator
    def _make_orchestrator():
        db = SessionLocal()
        return MultiAgentOrchestrator(db), db

    multi_orch, _orch_db = await loop.run_in_executor(None, _make_orchestrator)

    # Core engine components
    vad_manager = EnhancedVADManager(VADConfig(
        energy_threshold=int(os.getenv("VOICE_THRESHOLD", 40)),
        silence_timeout_ms=int(os.getenv("CALL_SILENCE_MS", 700))
    ))
    pipeline = VoicePipeline(PipelineConfig())

    call_id: str = str(__import__("uuid").uuid4())
    stream_sid = None
    caller_phone: str = ""
    current_language = "hi-IN"
    
    # Track stream state
    audio_buffer = bytearray()
    pipeline_task = None
    should_end_call = False

    try:
        while True:
            try:
                # Use a small timeout to allow checking task status
                message = await asyncio.wait_for(websocket.receive(), timeout=0.1)
                
                if "text" in message:
                    data = message["text"]
                    json_data = json.loads(data)
                    event_type = json_data.get("event")
                    
                    if event_type == "start":
                        stream_sid = json_data.get("start", {}).get("streamSid")
                        metadata   = json_data.get("start", {})
                        caller_phone = metadata.get("from", "")
                        call_id = stream_sid or call_id
                        logger.info(f"Stream started: sid={stream_sid} phone={caller_phone}")

                        # Find caller name
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

                        # Broadcast call start
                        asyncio.create_task(dashboard_manager.broadcast_call_state("active", call_id))

                        # Start session
                        def _start_session():
                            return multi_orch.start_call(
                                call_id=call_id,
                                phone_number=caller_phone,
                                known_name=known_caller_name,
                            )
                        greeting_text = await loop.run_in_executor(None, _start_session)
                        
                        # Pre-fetch buyer details in parallel
                        buyer_details_task = None
                        if caller_phone:
                            from app.services.bsb_api_client import fetch_buyer_details
                            buyer_details_task = asyncio.create_task(
                                fetch_buyer_details(caller_phone)
                            )

                        # Generate and play greeting
                        greeting_audio = await voice_service.text_to_speech(greeting_text)
                        if greeting_audio:
                            greeting_b64 = base64.b64encode(greeting_audio).decode('utf-8')
                            await websocket.send_text(json.dumps({
                                "event": "media", "streamSid": stream_sid, "media": {"payload": greeting_b64}
                            }))
                            
                            greeting_duration = len(greeting_audio) / 8000.0
                            vad_manager.start_playback_gate(greeting_duration)
                            
                            try:
                                asyncio.create_task(dashboard_manager.broadcast({
                                    "type": "transcript",
                                    "speaker": "agent",
                                    "text": greeting_text
                                }))
                            except Exception:
                                pass

                        # Collect pre-fetched details
                        if buyer_details_task:
                            try:
                                buyer_details = await asyncio.wait_for(buyer_details_task, timeout=10.0)
                                multi_orch.set_prefetched_buyer_details(call_id, buyer_details)
                            except Exception as e:
                                logger.warning(f"[PREFETCH] failed: {e}")

                    elif event_type == "media":
                        if should_end_call:
                            continue

                        payload = json_data.get("media", {}).get("payload")
                        if not payload:
                            continue

                        chunk = base64.b64decode(payload)
                        vad_result = vad_manager.process_frame(chunk)

                        if vad_result.is_barge_in:
                            logger.info("[STREAM] Barge-in detected! Cancelling current playback/pipeline.")
                            pipeline.cancel()
                            if pipeline_task and not pipeline_task.done():
                                pipeline_task.cancel()
                            audio_buffer.clear()
                            
                            # Send clear signal to Tata to stop current playback
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": stream_sid
                            }))

                        if vad_result.is_speech or vad_result.state == VADState.PLAYBACK_GATED:
                            # Accumulate audio. If gated, we just accumulate pre-roll.
                            if vad_result.state == VADState.SPEECH_STARTED and len(audio_buffer) == 0:
                                audio_buffer.extend(vad_manager.get_pre_roll())
                            audio_buffer.extend(chunk)

                        elif vad_result.state == VADState.SPEECH_ENDED:
                            if len(audio_buffer) > vad_manager.config.min_audio_bytes:
                                logger.info(f"[STREAM] Speech ended. Processing {len(audio_buffer)} bytes.")
                                
                                # Start pipeline processing
                                audio_to_process = bytes(audio_buffer)
                                audio_buffer.clear()
                                
                                async def run_pipeline(audio_data):
                                    nonlocal current_language, should_end_call
                                    try:
                                        # Use streaming turn
                                        async for tts_audio, sentence, is_final in pipeline.stream_turn(
                                            audio_bytes=audio_data,
                                            stt_func=voice_service.transcribe_audio,
                                            llm_stream_func=multi_orch.stream_response_sentences,
                                            tts_func=voice_service.text_to_speech,
                                            call_id=call_id,
                                            phone_number=caller_phone,
                                            language_code=current_language,
                                        ):
                                            if tts_audio:
                                                chunk_b64 = base64.b64encode(tts_audio).decode("utf-8")
                                                await websocket.send_text(json.dumps({
                                                    "event": "media", 
                                                    "streamSid": stream_sid, 
                                                    "media": {"payload": chunk_b64}
                                                }))
                                                
                                                # Gate VAD while this sentence plays
                                                play_duration = len(tts_audio) / 8000.0
                                                vad_manager.start_playback_gate(play_duration)
                                                
                                                try:
                                                    asyncio.create_task(dashboard_manager.broadcast({
                                                        "type": "transcript",
                                                        "speaker": "agent",
                                                        "text": sentence
                                                    }))
                                                except Exception:
                                                    pass

                                            if is_final:
                                                should_end_call = True
                                                
                                        # Broadcast metrics for this turn
                                        if pipeline.metrics_history:
                                            latest_metrics = pipeline.metrics_history[-1]
                                            asyncio.create_task(dashboard_manager.broadcast_metrics(
                                                stt_ms=latest_metrics.stt_ms,
                                                llm_ms=latest_metrics.llm_ms,
                                                tts_ms=latest_metrics.tts_ms,
                                                total_ms=latest_metrics.total_ms
                                            ))
                                                
                                    except Exception as e:
                                        logger.error(f"Pipeline error: {e}")

                                pipeline_task = asyncio.create_task(run_pipeline(audio_to_process))
                            else:
                                logger.debug("[STREAM] Audio buffer too small, ignoring.")
                                audio_buffer.clear()

                    elif event_type == "stop":
                        logger.info("Stream stopped by client")
                        break
                        
            except asyncio.TimeoutError:
                if should_end_call and (not pipeline_task or pipeline_task.done()):
                    # Give it a couple of seconds to finish playing audio before closing
                    await asyncio.sleep(2.0)
                    await websocket.send_text(json.dumps({"event": "stop", "streamSid": stream_sid}))
                    await asyncio.sleep(0.5)
                    break
                continue
                
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except Exception as e:
                if "receive" in str(e) and "disconnect" in str(e):
                    break
                logger.error(f"Error in loop: {e}", exc_info=True)
                
    except Exception as e:
        logger.error(f"Endpoint Error: {e}", exc_info=True)
    finally:
        logger.info("Cleaned up resources")
        asyncio.create_task(dashboard_manager.broadcast_call_state("completed", call_id))
        try:
            await voice_service.close()
        except Exception:
            pass

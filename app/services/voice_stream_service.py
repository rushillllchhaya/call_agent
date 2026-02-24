"""
Voice streaming service using direct Deepgram API calls
(works around Windows long path limitations with SDK)
"""
import asyncio
import logging
import json
import aiohttp
from app.core.config import settings

logger = logging.getLogger("voice_stream")

class VoiceStreamHandler:
    """
    Handles real-time voice streaming with Deepgram REST APIs.
    Simpler approach that doesn't require the full SDK.
    """
    
    def __init__(self):
        self.deepgram_api_key = settings.DEEPGRAM_API_KEY
        self.stt_base_url = "https://api.deepgram.com/v1/listen"
        self.tts_base_url = "https://api.deepgram.com/v1/speak"
    
    async def transcribe_audio(self, audio_data: bytes) -> str:
        """
        Transcribe audio using Deepgram REST API.
        
        Args:
            audio_data: Raw audio bytes
            
        Returns:
            Transcribed text
        """
        try:
            headers = {
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "audio/mulaw"  # Tata Smartflo sends μ-law (G.711) encoded audio
            }
            
            params = {
                "model": "nova-2",
                "language": "en-IN",
                "smart_format": "true",
                "punctuate": "true",
                "encoding": "mulaw",  # Explicitly tell Deepgram this is μ-law encoded
                "sample_rate": "8000",  # 8kHz telephony audio
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.stt_base_url,
                    headers=headers,
                    params=params,
                    data=audio_data,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        transcript = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
                        logger.info(f"STT Transcript: {transcript}")
                        return transcript
                    else:
                        error_text = await response.text()
                        logger.error(f"STT Error {response.status}: {error_text}")
                        return ""
                        
        except Exception as e:
            logger.error(f"STT Error: {e}")
            return ""
    
    async def text_to_speech(self, text: str) -> bytes:
        """
        Convert text to speech using Deepgram REST API.
        
        Args:
            text: Text to convert
            
        Returns:
            Audio bytes in linear PCM format
        """
        try:
            logger.info(f"TTS Request: {text}")
            
            headers = {
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "text/plain"
            }
            
            params = {
                "model": "aura-asteria-en",
                "sample_rate": "8000",
                "encoding": "mulaw"  # μ-law encoding for telephony compatibility
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.tts_base_url,
                    headers=headers,
                    params=params,
                    data=text,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info(f"TTS Success: {len(audio_data)} bytes")
                        return audio_data
                    else:
                        error_text = await response.text()
                        logger.error(f"TTS Error {response.status}: {error_text}")
                        return b""
                        
        except Exception as e:
            logger.error(f"TTS Error: {e}")
            return b""

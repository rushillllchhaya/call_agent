import asyncio
import logging
import aiohttp
import json
import base64
import audioop
import io
import wave
import re
from app.core.config import settings
from sarvamai import AsyncSarvamAI

logger = logging.getLogger("sarvam_service")

# ── TTS Abbreviation Preprocessor ─────────────────────────────────────────────
# Pass 1: Collapse any full chemical names → abbreviation (safety net in case AI slips)
# Pass 2: Expand abbreviations → space-separated letters for smooth TTS reading
_FULLNAME_TO_ABBR: list[tuple[str, str]] = [
    (r'\bLinear Low[\s-]?Density Polyethylene\b', 'LLDPE'),
    (r'\bLow[\s-]?Density Polyethylene\b',        'LDPE'),
    (r'\bHigh[\s-]?Density Polyethylene\b',        'HDPE'),
    (r'\bPolyethylene Terephthalate\b',            'PET'),
    (r'\bPolyvinyl Chloride\b',                    'PVC'),
    (r'\bPolypropylene\b',                         'PP'),
    (r'\bPolystyrene\b',                           'PS'),
    (r'\bPolyethylene\b',                          'PE'),
]

_TTS_REPLACEMENTS: list[tuple[str, str]] = [
    # Polymer types — hyphen-separated so TTS reads each letter smoothly without stutter
    (r'\bLLDPE\b',  'L-L-D-P-E'),
    (r'\bLDPE\b',   'L-D-P-E'),
    (r'\bHDPE\b',   'H-D-P-E'),
    (r'\bPVC\b',    'P-V-C'),
    (r'\bPET\b',    'P-E-T'),
    (r'\bPP\b',     'P-P'),
    (r'\bEPS\b',    'E-P-S'),
    (r'\bABS\b',    'A-B-S'),
    (r'\bPS\b',     'P-S'),
    (r'\bPE\b',     'P-E'),
    (r'\bNylon\b',  'Nylon'),     # already speakable
    # Companies / brands
    (r'\bIOCL\b',   'I-O-C-L'),
    (r'\bGAIL\b',   'गेल'),       # pronounce as Hindi word (natural)
    (r'\bHPCL\b',   'H-P-C-L'),
    (r'\bMRPL\b',   'M-R-P-L'),
    (r'\bOPAL\b',   'Opal'),      # already speakable
    (r'\bBSB\b',    'B-S-B'),
    # Units & common terms
    (r'\bMT\b',     'M-T'),
    (r'\bKG\b',     'K-G'),
    (r'\bMFI\b',    'M-F-I'),
    (r'\bGST\b',    'GST'),       # Hindi speakers know this word
    (r'\bRM\b',     'R-M'),
]

def _preprocess_for_tts(text: str) -> str:
    """
    Two-pass preprocessor to ensure polymer names are NEVER spoken as full words:
      Pass 1 – Collapse full chemical names → abbreviation (safety net for AI slip-ups)
               e.g. "High Density Polyethylene" → "HDPE"
      Pass 2 – Expand abbreviations → space-separated letters for smooth TTS
               e.g. "HDPE" → "H D P E"
    """
    # Pass 1: full name → abbreviation
    for pattern, replacement in _FULLNAME_TO_ABBR:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Pass 2: abbreviation → space-separated letters
    for pattern, replacement in _TTS_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Clean up any double spaces
    text = re.sub(r'  +', ' ', text)
    return text.strip()

class SarvamService:
    """
    Handles interactions with Sarvam AI API for TTS and STT.
    Includes audio transcoding (WAV -> Mu-Law 8kHz).
    """

    def __init__(self):
        self.api_key = settings.SARVAM_API_KEY
        self.tts_url = "https://api.sarvam.ai/text-to-speech"
        self.raw_stt_url = "https://api.sarvam.ai/speech-to-text"
        self.client = AsyncSarvamAI(api_subscription_key=self.api_key)
        
    async def text_to_speech(self, text: str, language_code: str = "hi-IN") -> bytes:
        """
        Convert text to speech using Sarvam AI.
        language_code: 'hi-IN' | 'gu-IN' | 'en-IN'
        Returns Mu-Law encoded 8kHz audio bytes for Tata Smartflo.
        """
        # Map detected language to a Sarvam-supported TTS language
        lang_map = {
            "hi-IN": "hi-IN",
            "gu-IN": "gu-IN",
            "en-IN": "en-IN",
        }
        tts_lang = lang_map.get(language_code, "hi-IN")  # default to Hindi if unknown

        # Expand abbreviations so TTS reads them smoothly (no stuttering)
        text = _preprocess_for_tts(text)

        # Guard: skip empty / whitespace-only responses (avoids Sarvam 400 error)
        if not text or not text.strip():
            logger.warning("TTS skipped: empty text after preprocessing")
            return b""

        try:
            logger.info(f"Sarvam TTS Request [{tts_lang}]: {text}")
            
            headers = {
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json"
            }
            
            payload = {
                "inputs": [text],
                "target_language_code": tts_lang,
                "speaker": "shruti",
                "model": "bulbul:v3",
                "pace": 1.1,
                "speech_sample_rate": 8000,
                "enable_preprocessing": True,
                "output_audio_codec": "wav"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.tts_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Sarvam TTS Response Keys: {data.keys()}")
                        
                        audio_b64 = data.get("audios", [])[0]
                        wav_bytes = base64.b64decode(audio_b64)
                        logger.info(f"Received {len(wav_bytes)} bytes from Sarvam")
                        logger.info(f"First 20 bytes: {wav_bytes[:20]}")
                        
                        return self._transcode_wav_to_mulaw(wav_bytes)
                    else:
                        error_text = await response.text()
                        logger.error(f"Sarvam TTS Error {response.status}: {error_text}")
                        return b""
                        
        except Exception as e:
            logger.error(f"Sarvam TTS Exception: {e}")
            return b""

    def connect_stt_stream(self, language_code: str = "hi-IN"):
        """
        Connect to Sarvam's streaming STT using their SDK.
        Strictly limited to Hindi, Gujarati, English.
        """
        # LANGUAGE TRIO LOCKDOWN: Fallback to hi-IN if unauthorized language requested
        VALID_LANGS = ["hi-IN", "gu-IN", "en-IN"]
        stt_lang = language_code if language_code in VALID_LANGS else "hi-IN"
        
        try:
            ws_cm = self.client.speech_to_text_streaming.connect(
                model="saaras:v3",
                mode="codemix",
                language_code=stt_lang,
                sample_rate=8000,
                high_vad_sensitivity=True,
                flush_signal=True,
                vad_signals=True
            )
            return ws_cm
        except Exception as e:
            logger.error(f"Failed to connect to Sarvam STT ({stt_lang}): {e}")
            return None

    async def transcribe_audio(self, audio_data: bytes, language_code: str = "hi-IN") -> tuple[str, str]:
        """
        Transcribe audio using Sarvam AI (REST fallback).
        Strictly limited to Hindi, Gujarati, English.
        """
        # Map to valid trio
        VALID_LANGS = ["hi-IN", "gu-IN", "en-IN"]
        stt_lang = language_code if language_code in VALID_LANGS else "hi-IN"
        
        try:
            wav_buffer = self._create_wav_from_mulaw(audio_data)
            
            form = aiohttp.FormData()
            form.add_field('params', json.dumps({
                "model": "saaras:v3",
                "language_code": stt_lang,
                "with_diarization": False
            }))
            form.add_field('file', wav_buffer, filename="audio.wav", content_type="audio/wav")

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.sarvam.ai/speech-to-text", 
                    headers={"api-subscription-key": self.api_key},
                    data=form
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        detected_lang = data.get("language_code", "")
                        transcript    = data.get("transcript", "")
                        return transcript, detected_lang
                    else:
                        text = await response.text()
                        logger.error(f"Sarvam STT Error: {text}")
                        return "", ""
        except Exception as e:
            logger.error(f"Sarvam STT Exception: {e}")
            return "", ""
        return "", ""

    def _transcode_mulaw_to_pcm16(self, mulaw_data: bytes) -> bytes:
        """Transcodes 8kHz Mu-Law to 8kHz 16-bit PCM."""
        return audioop.ulaw2lin(mulaw_data, 2)

    def _transcode_wav_to_mulaw(self, wav_data: bytes) -> bytes:
        """
        Extracts PCM data from WAV and converts to u-law.
        Sarvam returns 8kHz WAV (if requested), so likely 16-bit PCM.
        """
        try:
            with io.BytesIO(wav_data) as wav_file:
                with wave.open(wav_file, 'rb') as w:
                    # Verify params
                    if w.getnchannels() != 1 or w.getframerate() != 8000:
                        logger.warning(f"Unexpected WAV format: {w.getnchannels()}ch, {w.getframerate()}Hz")
                        # Perform conversion if needed (not implemented for prototype complexity)
                    
                    frames = w.readframes(w.getnframes())
                    # Convert 16-bit PCM to u-law using audioop
                    # width=2 for 16-bit
                    mulaw_data = audioop.lin2ulaw(frames, 2)
                    return mulaw_data
        except Exception as e:
            logger.error(f"Transcoding Error: {e}")
            return b""

    def _create_wav_from_mulaw(self, mulaw_data: bytes) -> bytes:
        """
        Wraps raw u-law bytes into a WAV container for upload.
        Or converts to PCM WAV if API doesn't support u-law WAV.
        """
        try:
            # Convert u-law to 16-bit PCM first (safer compatibility)
            pcm_data = audioop.ulaw2lin(mulaw_data, 2)
            
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2) # 16-bit
                w.setframerate(8000)
                w.writeframes(pcm_data)
            
            buffer.seek(0)
            return buffer.read()
        except Exception as e:
            logger.error(f"WAV Creation Error: {e}")
            return b""

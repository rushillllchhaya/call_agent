import os
import requests
import hashlib
from pathlib import Path
from app.core.config import settings

class TTSService:
    """
    Text-to-Speech service using Deepgram API.
    Converts text to audio and saves to static files.
    """
    
    def __init__(self):
        self.api_key = settings.DEEPGRAM_API_KEY
        self.api_url = "https://api.deepgram.com/v1/speak"
        self.static_dir = Path("app/static/audio")
        self.static_dir.mkdir(parents=True, exist_ok=True)
    
    def text_to_speech(self, text: str, voice: str = "aura-asteria-en") -> str | None:
        """
        Convert text to speech using Deepgram TTS.
        
        Args:
            text: The text to convert to speech
            voice: Deepgram voice model to use (default: aura-asteria-en - female voice)
                   Options: aura-asteria-en, aura-luna-en, aura-stella-en, aura-athena-en, aura-hera-en
        
        Returns:
            Relative path to the generated audio file (e.g., "static/audio/abc123.mp3")
            Returns None if TTS generation fails
        """
        # Generate cache filename based on text hash
        text_hash = hashlib.md5(text.encode()).hexdigest()
        filename = f"{text_hash}.mp3"
        file_path = self.static_dir / filename
        
        # Return cached file if exists
        if file_path.exists():
            return f"static/audio/{filename}"
        
        # Call Deepgram TTS API
        # Correct format: POST body contains text, model in query params
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "text/plain"
        }
        
        params = {
            "model": voice
        }
        
        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                data=text,  # Send text as plain text body, not JSON
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            # Save audio to file
            with open(file_path, "wb") as f:
                f.write(response.content)
            
            return f"static/audio/{filename}"
            
        except requests.exceptions.RequestException as e:
            print(f"TTS Error: {e}")
            if hasattr(e.response, 'text'):
                print(f"TTS Error Details: {e.response.text}")
            # Return None to indicate failure
            return None
    
    def get_audio_url(self, text: str, base_url: str) -> str | None:
        """
        Get full URL for audio file.
        
        Args:
            text: Text to convert
            base_url: Base URL of the application (e.g., ngrok URL)
        
        Returns:
            Full URL to audio file, or None if TTS fails
        """
        relative_path = self.text_to_speech(text)
        if relative_path:
            return f"{base_url}/{relative_path}"
        return None

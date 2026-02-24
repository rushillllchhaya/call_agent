from langdetect import detect
from deep_translator import GoogleTranslator

class LanguageHandler:
    @staticmethod
    def normalize_text(text: str) -> str:
        """
        Detects if text is not English (e.g., Hindi/Gujarati) and translates it.
        Returns the English text.
        """
        if not text or len(text.strip()) < 3:
            return text

        # Fast-path: pure ASCII text is already English — skip detection entirely
        # This prevents langdetect from mis-classifying "Yes.", "No.", "Ok" as French etc.
        if all(ord(c) < 128 for c in text):
            return text

        # Only run detection on longer, non-ASCII strings (avoids false positives)
        if len(text.strip()) < 10:
            return text

        try:
            lang = detect(text)
            # If detected as a non-English language, translate to English
            if lang != "en":
                print(f"DEBUG: Detected language '{lang}'. Translator engaging...")
                translated = GoogleTranslator(source='auto', target='en').translate(text)
                print(f"DEBUG: Translated '{text}' -> '{translated}'")
                return translated
            return text
        except Exception as e:
            print(f"WARNING: Language detection failed: {e}")
            return text

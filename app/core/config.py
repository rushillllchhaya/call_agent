from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Voice AI Pipeline"
    DATABASE_URL: str = "sqlite:///./conversations.db"
    GROQ_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""
    SARVAM_API_KEY: str = ""

    # ── BSB HTTP API endpoints ────────────────────────────────────────────────
    BSB_API_BASE: str = "https://backend.bsbinfotrade.com"
    BSB_BUYER_DETAILS_URL:  str = "https://backend.bsbinfotrade.com/api/web/get-buyer-lead"
    BSB_GET_PRICE_URL:      str = "https://backend.bsbinfotrade.com/api/web/seller-offer-list"
    BSB_SAVE_DETAILS_URL:   str = "https://backend.bsbinfotrade.com/api/web/save-buyer-lead"
    BSB_WHATSAPP_URL:       str = "https://whatsapp.bsbinfotrade.com/api/v2/messages/template"
    BSB_WHATSAPP_TOKEN:     str = "wm_aNMFbDuiNjBRwpfKSipVVtArsOqbTcqImEgu4PnbCz1aKOREw0N3fkqBSzCr"

    # ── WhatsApp static params ────────────────────────────────────────────────
    BSB_WA_TEMPLATE:     str = "ai_call_buyer_message"
    BSB_WA_LANGUAGE:     str = "en"
    BSB_WA_HEADER_VIDEO: str = "https://backend.bsbinfotrade.com/ai_static/video842026.mp4"

    # ── Zoho CRM credentials ──────────────────────────────────────────────────
    ZOHO_CLIENT_ID:     str = "1000.5INWIW3DHWVC75TUXT39Z24FHS5LWA"
    ZOHO_CLIENT_SECRET: str = "bf8fd09da0ee9641ad188630f62bfe16ca8ac5a2cc"
    ZOHO_REFRESH_TOKEN: str = "bf8fd09da0ee9641ad188630f62bfe16ca8ac5a2cc"
    ZOHO_ACCOUNTS_URL:  str = "https://accounts.zoho.in"
    ZOHO_API_URL:       str = "https://www.zohoapis.in"

    # ── Ngrok (used by docker-compose — not needed by app code directly) ──────
    NGROK_AUTHTOKEN: str = ""
    NGROK_DOMAIN:    str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"   # silently ignore any .env vars not declared above

settings = Settings()


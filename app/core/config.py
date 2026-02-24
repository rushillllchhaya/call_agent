from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Voice AI Pipeline"
    DATABASE_URL: str = "sqlite:///./conversations.db"
    GROQ_API_KEY: str
    DEEPGRAM_API_KEY: str
    SARVAM_API_KEY: str
    
    class Config:
        env_file = ".env"

settings = Settings()

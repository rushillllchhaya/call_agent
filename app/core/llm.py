"""
Centralized LLM Configuration
=============================
Provides a standardized factory for instantiating ChatGroq models across all agents.
Ensures consistent timeouts, error handling wrappers, and model strings.
"""
import logging
from langchain_groq import ChatGroq
from app.core.config import settings

logger = logging.getLogger("llm_config")

# Default production models for Groq
DEFAULT_ROUTING_MODEL = "llama-3.3-70b-versatile"
DEFAULT_CONVERSATION_MODEL = "llama-3.1-8b-instant"

def get_llm(
    model_name: str = DEFAULT_CONVERSATION_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 250,
) -> ChatGroq:
    """
    Returns a configured ChatGroq instance for BSB agents.
    Uses centralized settings for timeout, retries, and API keys.
    """
    if not settings.GROQ_API_KEY:
        logger.warning("[LLM] GROQ_API_KEY is not set! LLM calls will fail.")
        
    return ChatGroq(
        groq_api_key=settings.GROQ_API_KEY,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=15,  # Pipeline timeout is 18s; LLM should timeout at 15s to allow fallback
        max_retries=2,       # Langchain's built-in retry
    )

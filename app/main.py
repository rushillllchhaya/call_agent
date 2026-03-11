from fastapi import FastAPI
from app.core.config import settings
from app.core.database import engine, Base
# Import models to ensure they are registered with Base
from app.core import models 

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

from app.api.endpoints import router as api_router
from app.api.webhooks import router as webhook_router
from app.api.streams import router as streams_router
from app.api.call_agent import router as call_agent_router

from fastapi.staticfiles import StaticFiles

app.include_router(api_router,       prefix="/api/v1")
app.include_router(webhook_router,   prefix="/webhook")
app.include_router(streams_router,   prefix="/ws")
app.include_router(call_agent_router, prefix="/api/call")

app.mount("/static", StaticFiles(directory="app/static", html=True), name="static")

@app.on_event("startup")
async def startup_event():
    import threading

    # ── 1. Test Groq connection ───────────────────────────────────────────────
    from app.services.ai_agent import llm
    try:
        print("DEBUG: Testing Groq Connection...")
        # Groq requires the word 'json' in the prompt if json_mode is enabled
        response = llm.invoke("Respond with the word json: Hello")
        print(f"DEBUG: Groq Connection Successful. Response: {response.content}")
    except Exception as e:
        print(f"CRITICAL ERROR: Groq Connection Failed: {e}")
        # We don't exit here so the app still runs, but logs are clear.

    # ── 2. Pre-warm embedding model in background thread ─────────────────────
    # sentence-transformers downloads/loads the model (~80MB) the first time
    # it is used. If this happens mid-WebSocket-handshake it blocks the async
    # event loop and the connection appears as a plain HTTP GET in ngrok
    # (the WebSocket upgrade times out before the model finishes loading).
    # Loading it here — before any call comes in — prevents that entirely.
    def _preload_embedder():
        try:
            from app.services.semantic_cache import _get_embedder, _get_faiss_index
            _get_embedder()    # downloads + loads model (cached after first run)
            _get_faiss_index() # initialises FAISS index
            print("DEBUG: Embedding model pre-loaded — semantic cache ready.")
        except Exception as e:
            print(f"WARNING: Embedding model pre-load failed (non-fatal): {e}")

    threading.Thread(target=_preload_embedder, daemon=True).start()
    print("DEBUG: Embedding model pre-load started in background thread.")


@app.get("/")
def read_root():
    return {"message": "Voice AI Backend Running. Go to /static/index.html"}


@app.get("/health")
def health_check():
    return {"status": "ok"}

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

from fastapi.staticfiles import StaticFiles

app.include_router(api_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/webhook")
app.include_router(streams_router, prefix="/ws")

app.mount("/static", StaticFiles(directory="app/static", html=True), name="static")

@app.on_event("startup")
async def startup_event():
    from app.services.ai_agent import llm
    try:
        print("DEBUG: Testing Groq Connection...")
        # Groq requires the word 'json' in the prompt if json_mode is enabled
        response = llm.invoke("Respond with the word json: Hello")
        print(f"DEBUG: Groq Connection Successful. Response: {response.content}")
    except Exception as e:
        print(f"CRITICAL ERROR: Groq Connection Failed: {e}")
        # We don't exit here so the app still runs, but logs are clear.

@app.get("/")
def read_root():
    return {"message": "Voice AI Backend Running. Go to /static/index.html"}


@app.get("/health")
def health_check():
    return {"status": "ok"}

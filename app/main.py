from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from app.core.config import settings
from app.core.database import engine, Base
from app.core import models

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

from app.api.endpoints import router as api_router
from app.api.webhooks import router as webhook_router
from app.api.streams import router as streams_router

from fastapi.staticfiles import StaticFiles

app.include_router(api_router,       prefix="/api/v1")
app.include_router(webhook_router,   prefix="/webhook")
app.include_router(streams_router,   prefix="/ws")

# Mount Static and Dashboard
import os
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static", html=True), name="static")

if os.path.exists("app/dashboard"):
    app.mount("/dashboard", StaticFiles(directory="app/dashboard", html=True), name="dashboard")

# ── Dashboard WebSocket (imports singleton from services/dashboard.py) ────────
from app.services.dashboard import dashboard_manager

@app.websocket("/dashboard-ws")
async def dashboard_websocket(websocket: WebSocket):
    await dashboard_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_manager.disconnect(websocket)

@app.on_event("startup")
async def startup_event():
    pass

@app.get("/")
def read_root():
    return {"message": "Voice AI Backend Running. Go to /dashboard/index.html"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

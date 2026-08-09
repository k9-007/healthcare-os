import asyncio
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import SessionLocal, ensure_schema
from .routers import (
    analytics, brain, calls, careplans, documents, patients, schedule,
    twilio_webhooks, voice_ws,
)
from .seed import seed_if_empty
from .services import scheduler
from .services.telephony import effective_mode
from .services.voice import vad

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    if settings.seed_on_startup:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    scheduler.start()
    # Load Silero now so the first live call doesn't pay the model-load cost.
    await asyncio.to_thread(vad.warmup)
    logger.info(
        "HealthcareOS backend up — telephony=%s, voice=%s, sarvam=%s",
        effective_mode(), settings.voice_mode,
        "configured" if settings.sarvam_configured else "MISSING (fallback mode)",
    )
    if settings.public_base_url_is_local:
        logger.warning(
            "PUBLIC_BASE_URL=%s is not reachable from Twilio. For a real call run "
            "`ngrok http 8000` and set PUBLIC_BASE_URL to the https URL it prints.",
            settings.public_base_url,
        )
    else:
        logger.info("Twilio console test URL: %s/twilio/voice/demo", settings.public_base_url)
    yield
    scheduler.shutdown()


app = FastAPI(
    title="HealthcareOS API",
    description="The AI Care Coordination Layer for Bharat — Brain (cited clinical Q&A) + "
                "Patient Care+ (autonomous multilingual voice follow-up).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# TTS audio, uploads and recordings served for the SPA's audio players
app.mount("/data", StaticFiles(directory=str(settings.data_path)), name="data")


@app.get("/voice-console", include_in_schema=False)
def voice_console():
    """Hold a real conversation with the agent using a browser mic.

    Same VoiceAgent as a phone call — useful for demos and for testing the turn
    loop without a carrier, tunnel, or Twilio spend.
    """
    return FileResponse(Path(__file__).parent / "static" / "voice_console.html")

app.include_router(patients.router)
app.include_router(careplans.router)
app.include_router(documents.router)
app.include_router(brain.router)
app.include_router(calls.router)
app.include_router(schedule.router)
app.include_router(analytics.router)
app.include_router(twilio_webhooks.router)
app.include_router(voice_ws.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"internal error: {type(exc).__name__}"})


@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "telephony_mode": effective_mode(),
        "voice_mode": settings.voice_mode,
        "sarvam_configured": settings.sarvam_configured,
        "time_scale_demo": settings.time_scale_demo,
        "public_base_url": settings.public_base_url,
        "public_url_reachable": not settings.public_base_url_is_local,
        "twilio_console_twiml_url": f"{settings.public_base_url}/twilio/voice/demo",
    }

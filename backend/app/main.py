import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import Base, SessionLocal, engine
from .routers import analytics, brain, calls, careplans, documents, patients, plivo_webhooks, schedule
from .seed import seed_if_empty
from .services import scheduler
from .services.telephony import effective_mode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if settings.seed_on_startup:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    scheduler.start()
    logger.info(
        "HealthcareOS backend up — telephony=%s, sarvam=%s",
        effective_mode(), "configured" if settings.sarvam_configured else "MISSING (fallback mode)",
    )
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

app.include_router(patients.router)
app.include_router(careplans.router)
app.include_router(documents.router)
app.include_router(brain.router)
app.include_router(calls.router)
app.include_router(schedule.router)
app.include_router(analytics.router)
app.include_router(plivo_webhooks.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"internal error: {type(exc).__name__}"})


@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "telephony_mode": effective_mode(),
        "sarvam_configured": settings.sarvam_configured,
        "time_scale_demo": settings.time_scale_demo,
    }

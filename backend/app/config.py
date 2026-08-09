from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Sarvam
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"

    # Telephony
    telephony_mode: str = "simulation"  # "twilio" | "simulation"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    public_base_url: str = "http://localhost:8000"

    # Storage
    database_url: str = "sqlite:///./data/healthcareos.db"
    data_dir: str = "./data"

    # Scheduler
    sched_tick_seconds: int = 60
    schedule_horizon_hours: int = 48
    time_scale_demo: bool = False  # ask_after_days interpreted as minutes for demos

    # Misc
    default_language: str = "hi-IN"
    seed_on_startup: bool = True
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    max_upload_mb: int = 25

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def audio_dir(self) -> Path:
        return self.data_path / "audio"

    @property
    def uploads_dir(self) -> Path:
        return self.data_path / "uploads"

    @property
    def recordings_dir(self) -> Path:
        return self.data_path / "recordings"

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_path, s.audio_dir, s.uploads_dir, s.recordings_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s

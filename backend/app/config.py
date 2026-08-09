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
    twilio_account_sid: str = ""  # must be the AC… Account SID
    twilio_auth_token: str = ""  # account auth token, or the API key secret
    twilio_api_key_sid: str = ""  # optional SK… API key; auth token is then its secret
    twilio_from_number: str = ""
    public_base_url: str = "http://localhost:8000"

    # Real-time voice (VAD endpointing + turn taking)
    voice_mode: str = "stream"  # "stream" = conversation | "classic" = <Play>+<Record>
    # Trial accounts strip <Stream> from TwiML and replace it with a spoken
    # "not available on trial accounts", so a real call gets no conversation.
    # <Play> is still allowed, which at least lets the patient hear the script.
    twilio_trial_account: bool = False
    vad_speech_threshold: float = 0.5  # Silero probability for a frame to count as speech
    vad_start_ms: int = 150  # sustained speech before the patient is "talking"
    vad_silence_ms: int = 600  # trailing silence that ends a turn — the main feel knob
    vad_max_utterance_ms: int = 15000  # hard stop so a monologue still gets processed
    vad_barge_in_ms: int = 400  # speech over the nurse before we cut her off
    voice_no_speech_ms: int = 7000  # silence after a question before re-prompting

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
        # Twilio always addresses calls under the AC… account, even when
        # authenticating with an SK… API key.
        return bool(
            self.twilio_account_sid.startswith("AC")
            and self.twilio_auth_token
            and self.twilio_from_number
        )

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def public_base_url_is_local(self) -> bool:
        """Twilio can only reach a public host — localhost means no tunnel yet."""
        return any(h in self.public_base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))

    @property
    def public_ws_base_url(self) -> str:
        base = self.public_base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :]
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :]
        return base


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_path, s.audio_dir, s.uploads_dir, s.recordings_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s

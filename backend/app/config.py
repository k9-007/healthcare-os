from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Sarvam
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"

    # Telephony
    telephony_mode: str = "simulation"  # "plivo" | "simulation"
    plivo_auth_id: str = ""  # MA…/SA… Auth ID from the Plivo console
    plivo_auth_token: str = ""
    plivo_from_number: str = ""  # a voice-enabled Plivo number, E.164
    public_base_url: str = "http://localhost:8000"

    # Real-time voice — Silero VAD endpointing + turn taking.
    # "stream": bidirectional Plivo Audio Stream (phone) / browser WS — Silero VAD
    # "classic": Plivo <Play>+<Record> with carrier silence timeout (no barge-in)
    voice_mode: str = "stream"  # "stream" | "classic"
    vad_speech_threshold: float = 0.5  # Silero probability for a frame to count as speech
    vad_start_ms: int = 150  # sustained speech before the patient is "talking"
    # Trailing silence that ends a turn. ~400–600ms is the sweet spot: lower
    # and Hindi mid-phrase pauses cut off; higher and the call feels laggy.
    vad_silence_ms: int = 500
    vad_max_utterance_ms: int = 15000  # hard stop so a monologue still gets processed
    # Speech over the nurse before we cut her off. Deliberately far longer than
    # `vad_start_ms`: a ringtone tail, a carrier announcement or a cough will
    # clear 350ms and truncate the greeting, and a patient who never hears the
    # question spends the rest of the call asking what was said.
    vad_barge_in_ms: int = 700
    # Utterances quieter than this are line noise, not an answer. STT invents
    # plausible sentences for near-silence, so it must not see them.
    vad_min_utterance_rms: int = 250
    voice_no_speech_ms: int = 7000  # silence after a question before re-prompting

    # Storage
    database_url: str = "sqlite:///./data/healthcareos.db"
    data_dir: str = "./data"

    # Scheduler
    sched_tick_seconds: int = 60
    schedule_horizon_hours: int = 48
    time_scale_demo: bool = False  # ask_after_days interpreted as minutes for demos

    # Transcribe over Sarvam's WebSocket while the patient is still speaking
    # instead of uploading the finished utterance. Measured 570-1137 ms faster
    # per turn; off by default until it has been proven on real calls.
    stt_streaming: bool = False

    # Spoken aloud when a patient asks who is calling. Left blank the nurse
    # says "the hospital" — she must never name an organization we invented.
    hospital_name: str = ""

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
    def plivo_configured(self) -> bool:
        return bool(self.plivo_auth_id and self.plivo_auth_token and self.plivo_from_number)

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def public_base_url_is_local(self) -> bool:
        """Plivo can only reach a public host — localhost means no tunnel yet."""
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

"""Sarvam API client — every capability the platform uses, with retries and
graceful degradation.

All methods raise SarvamUnavailable when the API key is missing or the API is
unreachable after retries; callers are expected to catch it and fall back to
deterministic local behaviour so the demo never hard-fails.
"""

import asyncio
import base64
import io
import json
import logging
import time
import uuid
import wave
import zipfile
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger("sarvam")

# TTS hard limit is ~500 chars per call; keep headroom.
TTS_CHUNK_CHARS = 450
# Languages supported by bulbul TTS (subset of the 23 STT/translate languages).
DEFAULT_SPEAKER = "priya"

# sarvam-105b reasons before answering: high quality, tens of seconds.
REASONING_MODEL = "sarvam-105b"
# Answers directly in well under a second — the only viable model mid-call.
CONVERSATION_MODEL = "sarvam-105b-conversations"
# Telephony sample rate; TTS renders natively at 8 kHz so nothing is resampled.
TELEPHONY_SAMPLE_RATE = 8000

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


class SarvamUnavailable(Exception):
    """Raised when Sarvam cannot be used — callers must degrade gracefully."""


class SarvamClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base = self.settings.sarvam_base_url.rstrip("/")

    # ---------- plumbing ----------

    def _headers(self) -> dict[str, str]:
        if not self.settings.sarvam_configured:
            raise SarvamUnavailable("SARVAM_API_KEY not configured")
        return {"api-subscription-key": self.settings.sarvam_api_key}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        timeout: float = 60.0,
        retries: int = MAX_RETRIES,
    ) -> Any:
        headers = self._headers()
        url = path if path.startswith("http") else f"{self.base}{path}"
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(
                        method, url, headers=headers, json=json_body, data=data, files=files
                    )
                if resp.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                if resp.status_code >= 400:
                    raise SarvamUnavailable(
                        f"Sarvam {path} failed [{resp.status_code}]: {resp.text[:300]}"
                    )
                return resp.json()
            except SarvamUnavailable:
                raise
            except Exception as e:  # network errors + retryable statuses
                last_err = e
                if attempt < retries:
                    delay = 1.5 * (2 ** (attempt - 1))
                    logger.warning("Sarvam %s attempt %d failed (%s); retrying in %.1fs", path, attempt, e, delay)
                    await asyncio.sleep(delay)
        raise SarvamUnavailable(f"Sarvam {path} failed after {retries} attempts: {last_err}")

    # ---------- LLM ----------

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        model: str = REASONING_MODEL,
        timeout: float = 90.0,
        retries: int = MAX_RETRIES,
    ) -> str:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        out = await self._request(
            "POST", "/v1/chat/completions", json_body=body, timeout=timeout, retries=retries
        )
        try:
            choice = out["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError) as e:
            raise SarvamUnavailable(f"unexpected chat response shape: {e}")

        # sarvam-105b reasons before answering and bills reasoning_content against
        # the same completion budget, so `content` is null whenever the budget runs
        # out mid-thought. Treat that as unavailable rather than crashing.
        content = (message.get("content") or "").strip()
        if content:
            return content
        if choice.get("finish_reason") == "length":
            raise SarvamUnavailable(
                f"response truncated after {max_tokens} tokens of reasoning, no answer produced"
            )
        raise SarvamUnavailable("chat returned empty content")

    async def chat_json(self, messages: list[dict[str, str]], **kw) -> dict | list:
        """Chat call that must return JSON; tolerates code fences and prose around it."""
        raw = await self.chat(messages, **kw)
        return extract_json(raw)

    async def chat_fast(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 200,
        timeout: float = 8.0,
        retries: int = 1,
    ) -> str:
        """Sub-second chat for live calls.

        REASONING_MODEL thinks before answering and bills that thinking against
        the completion budget, so it needs thousands of tokens and tens of
        seconds — unusable while someone waits on the phone. CONVERSATION_MODEL
        answers directly in ~0.8s.
        """
        return await self.chat(
            messages, temperature=temperature, max_tokens=max_tokens,
            model=CONVERSATION_MODEL, timeout=timeout, retries=retries,
        )

    # ---------- Translate ----------

    async def translate(self, text: str, target_language: str, source_language: str = "en-IN") -> str:
        if not text.strip():
            return text
        if target_language.split("-")[0] == "en" and source_language.split("-")[0] == "en":
            return text
        body = {
            "input": text[:2000],
            # sarvam-translate:v1 rejects "auto" and only supports mode "formal"
            "source_language_code": source_language,
            "target_language_code": target_language,
            "model": "sarvam-translate:v1",
            "mode": "formal",
        }
        out = await self._request("POST", "/translate", json_body=body)
        return out.get("translated_text") or text

    # ---------- Language ID ----------

    async def identify_language(self, text: str) -> str:
        out = await self._request("POST", "/text-lid", json_body={"input": text[:1000]})
        return out.get("language_code") or ""

    # ---------- TTS ----------

    async def tts(
        self,
        text: str,
        language: str,
        speaker: str = DEFAULT_SPEAKER,
        *,
        sample_rate: int | None = None,
        timeout: float = 90.0,
        retries: int = MAX_RETRIES,
    ) -> bytes:
        """Synthesize speech, chunking around the per-request char limit,
        and return a single concatenated WAV byte blob."""
        chunks = split_for_tts(text, TTS_CHUNK_CHARS)
        wavs: list[bytes] = []
        for chunk in chunks:
            body: dict[str, Any] = {
                "text": chunk,
                "target_language_code": language,
                "speaker": speaker,
                "model": "bulbul:v3",
                "enable_preprocessing": True,
            }
            if sample_rate:
                body["speech_sample_rate"] = sample_rate
            out = await self._request(
                "POST", "/text-to-speech", json_body=body, timeout=timeout, retries=retries
            )
            audios = out.get("audios") or []
            if not audios:
                raise SarvamUnavailable("TTS returned no audio")
            wavs.extend(base64.b64decode(a) for a in audios)
        return concat_wavs(wavs)

    async def tts_to_file(self, text: str, language: str, speaker: str = DEFAULT_SPEAKER) -> str:
        """TTS and persist under DATA_DIR/audio; returns path relative to DATA_DIR."""
        audio = await self.tts(text, language, speaker)
        name = f"tts_{int(time.time())}_{uuid.uuid4().hex[:8]}.wav"
        path = self.settings.audio_dir / name
        path.write_bytes(audio)
        return f"audio/{name}"

    async def tts_telephony(
        self, text: str, language: str, speaker: str = DEFAULT_SPEAKER, *, timeout: float = 20.0
    ) -> bytes:
        """WAV rendered at 8 kHz for the live call path — no resampling, and a
        short timeout because a hung TTS call is dead air on the phone."""
        return await self.tts(
            text, language, speaker,
            sample_rate=TELEPHONY_SAMPLE_RATE, timeout=timeout, retries=2,
        )

    # ---------- STT ----------

    async def stt(
        self,
        audio_bytes: bytes,
        filename: str = "reply.wav",
        language_hint: str | None = None,
        *,
        timeout: float = 120.0,
        retries: int = MAX_RETRIES,
    ) -> tuple[str, str, float]:
        """Returns (transcript, detected_language, confidence)."""
        data: dict[str, str] = {"model": "saarika:v2.5"}
        if language_hint:
            data["language_code"] = language_hint
        files = {"file": (filename, audio_bytes, guess_mime(filename))}
        out = await self._request(
            "POST", "/speech-to-text", data=data, files=files, timeout=timeout, retries=retries
        )
        transcript = out.get("transcript") or ""
        lang = out.get("language_code") or language_hint or ""
        conf = 0.0
        # confidence may arrive in different shapes depending on model/mode
        raw_conf = out.get("language_confidence") or out.get("confidence")
        if isinstance(raw_conf, (int, float)):
            conf = float(raw_conf)
        return transcript, lang, conf

    # ---------- Text analytics (typed Q&A over a transcript) ----------

    async def text_analytics(self, text: str, questions: list[dict]) -> list[dict]:
        """questions: [{id, text, type, properties?}] → [{id, response}]"""
        data = {"text": text[:8000], "questions": json.dumps(questions)}
        out = await self._request("POST", "/text-analytics", data=data, timeout=90.0)
        return out.get("answers") or []

    async def text_analytics_llm(self, text: str, questions: list[dict]) -> list[dict]:
        """Same typed Q&A contract as text_analytics, answered by the chat model.

        /text-analytics is not enabled on every Sarvam plan, so this keeps the
        structured-extraction quality of an LLM instead of dropping straight to
        keyword matching. Returns the identical [{id, response}] shape.
        """
        spec = "\n".join(f"- id={q.get('id')} :: {q.get('text')}" for q in questions)
        out = await self.chat_json(
            [
                {"role": "system", "content": (
                    "You extract structured clinical facts from a patient's spoken reply to a "
                    "follow-up call. Answer every question using only what the patient said; "
                    "never invent details. Keep each response short. "
                    'Respond with pure JSON and nothing else: {"answers":[{"id":str,"response":str}]}'
                )},
                {"role": "user", "content": f"PATIENT REPLY:\n{text[:4000]}\n\nQUESTIONS:\n{spec}"},
            ],
            temperature=0.1,
        )
        answers = out.get("answers") if isinstance(out, dict) else out
        return [a for a in (answers or []) if isinstance(a, dict)]

    # ---------- Vision document digitization (async job) ----------

    async def vision_extract(self, file_path: str, language: str = "en-IN") -> str:
        """PDF/image → markdown via the doc-digitization job pipeline."""
        p = Path(file_path)
        if not p.exists():
            raise SarvamUnavailable(f"file not found: {file_path}")
        headers = self._headers()

        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1. create job
            resp = await client.post(
                f"{self.base}/doc-digitization/job/v1",
                headers=headers,
                json={"language": language, "output_format": "md"},
            )
            if resp.status_code >= 400:
                raise SarvamUnavailable(f"vision job create failed [{resp.status_code}]: {resp.text[:300]}")
            job = resp.json()
            job_id = job.get("job_id") or job.get("id")
            if not job_id:
                raise SarvamUnavailable(f"vision job create: no job id in {str(job)[:200]}")

            # 2. upload to presigned target (shape differs across versions — probe common keys)
            upload_url = _dig(job, "upload_url") or _dig(job, "presigned_url") or _dig(job, "url")
            if upload_url:
                up = await client.put(upload_url, content=p.read_bytes())
                if up.status_code >= 400:
                    raise SarvamUnavailable(f"vision upload failed [{up.status_code}]")
            else:
                up = await client.post(
                    f"{self.base}/doc-digitization/job/v1/{job_id}/upload",
                    headers=headers,
                    files={"file": (p.name, p.read_bytes(), guess_mime(p.name))},
                )
                if up.status_code >= 400:
                    raise SarvamUnavailable(f"vision upload failed [{up.status_code}]: {up.text[:200]}")

            # 3. start
            st = await client.post(f"{self.base}/doc-digitization/job/v1/{job_id}/start", headers=headers)
            if st.status_code >= 400:
                raise SarvamUnavailable(f"vision start failed [{st.status_code}]: {st.text[:200]}")

            # 4. poll (bounded)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                s = await client.get(f"{self.base}/doc-digitization/job/v1/{job_id}/status", headers=headers)
                if s.status_code >= 400:
                    raise SarvamUnavailable(f"vision status failed [{s.status_code}]")
                state = (s.json().get("status") or s.json().get("state") or "").lower()
                if state in {"completed", "success", "succeeded"}:
                    out_url = _dig(s.json(), "output_url") or _dig(s.json(), "download_url")
                    if not out_url:
                        raise SarvamUnavailable("vision completed but no output url")
                    dl = await client.get(out_url)
                    return _md_from_output(dl.content)
                if state in {"failed", "error"}:
                    raise SarvamUnavailable(f"vision job failed: {s.text[:300]}")
                await asyncio.sleep(5)
        raise SarvamUnavailable("vision job timed out after 300s")


# ---------- helpers ----------

def split_for_tts(text: str, limit: int) -> list[str]:
    """Split on sentence boundaries so each chunk fits the TTS char limit."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text] if text else []
    import re
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        while len(s) > limit:  # pathological sentence longer than the limit
            chunks.append(s[:limit])
            s = s[limit:]
        if len(current) + len(s) + 1 <= limit:
            current = f"{current} {s}".strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


def concat_wavs(blobs: list[bytes]) -> bytes:
    """Concatenate WAV blobs into one file (frames appended, params from first)."""
    if len(blobs) == 1:
        return blobs[0]
    params = None
    frames: list[bytes] = []
    for blob in blobs:
        with wave.open(io.BytesIO(blob), "rb") as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)  # type: ignore[arg-type]
        for f in frames:
            out.writeframes(f)
    return buf.getvalue()


def extract_json(raw: str) -> dict | list:
    """Parse JSON out of an LLM reply that may include fences or prose."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # first {...} or [...] region
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_c), raw.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    repaired = _repair_truncated_json(raw)
    if repaired is not None:
        return repaired
    raise SarvamUnavailable(f"LLM did not return parseable JSON: {raw[:200]}")


def _repair_truncated_json(raw: str) -> dict | list | None:
    """Salvage a reply cut off mid-object by closing whatever is still open.

    Reasoning models can exhaust the token budget partway through a long string
    field; the leading keys are still usable, so close the structure and parse.
    """
    start = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    body = raw[start:]

    stack: list[str] = []
    in_string = escaped = False
    for ch in body:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()

    candidate = body
    if in_string:
        candidate += '"'
    # drop a dangling key or comma that would make the closed object invalid
    candidate = candidate.rstrip().rstrip(",")
    if candidate.rstrip().endswith(":"):
        candidate = candidate.rstrip()[:-1].rstrip().rstrip(",")
        if candidate.endswith('"'):
            candidate = candidate[: candidate.rfind('"', 0, len(candidate) - 1)].rstrip().rstrip(",")
    candidate += "".join(reversed(stack))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def guess_mime(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "wav": "audio/wav", "mp3": "audio/mpeg", "webm": "audio/webm", "ogg": "audio/ogg",
        "m4a": "audio/mp4", "pdf": "application/pdf", "png": "image/png",
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")


def _dig(obj: dict, key: str) -> str | None:
    """Find `key` anywhere in a nested dict."""
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], str):
            return obj[key]
        for v in obj.values():
            found = _dig(v, key) if isinstance(v, dict) else None
            if found:
                return found
    return None


def _md_from_output(content: bytes) -> str:
    """Vision output may be a ZIP of markdown files or raw markdown."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            parts = []
            for name in sorted(z.namelist()):
                if name.endswith((".md", ".txt")):
                    parts.append(z.read(name).decode("utf-8", errors="replace"))
            if parts:
                return "\n\n".join(parts)
    except zipfile.BadZipFile:
        pass
    return content.decode("utf-8", errors="replace")


sarvam = SarvamClient()

# HealthcareOS

**The AI care coordination layer for Bharat.**

Care shouldn't end at discharge — it should follow every patient home, in their own language.

HealthcareOS turns a hospital's verified documents into cited clinical answers, and turns treatment plans into autonomous multilingual voice follow-up. One closed loop: call → understand → escalate → doctor replies → callback.

| Pillar | What it does |
|---|---|
| **Brain** | Upload SOPs / guidelines → tree-indexed Q&A with citations (cite-or-refuse, no guessing) |
| **Patient Care+** | AI nurse voice calls for meds, symptoms, and recovery questions |
| **Smart scheduling** | Cron-driven calls per medicine, per patient, from the care plan |
| **Care Graph** | Explainable recovery timeline from discharge → recovered |
| **Escalation** | Urgent-symptom detection → nurse / doctor alert |
| **Closed loop** | Doctor's typed reply becomes an automatic callback in the patient's language |

Voice and language run on **[Sarvam](https://www.sarvam.ai/)** (STT · TTS · translate · LLM · doc digitization). Phone calls use **Plivo** turn-based IVR, with a zero-infra **simulation** mode for demos without a carrier.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python · FastAPI · SQLAlchemy · SQLite · APScheduler |
| Frontend | React · Vite · TypeScript · Tailwind · TanStack Query |
| Voice / LLM | Sarvam (`sarvam-105b`, `bulbul:v3`, `saarika:v2.5`) |
| Telephony | Plivo (real) · simulation (browser reply) |
| Languages | Hindi, English, Tamil, Kannada, Marathi, … (Sarvam Indic set) |

---

## Quick start

### Prerequisites

- Python 3.11+
- Node 20+
- A [Sarvam API key](https://www.sarvam.ai/) (optional for UI exploration — features fall back when the key is missing or out of credits)

### 1. Backend

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env   # paste SARVAM_API_KEY
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health: [http://localhost:8000/health](http://localhost:8000/health)

On first boot the DB is created and seeded (demo patients, care plans, indexed docs) when `SEED_ON_STARTUP=true`.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_BASE=http://localhost:8000
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

### 3. Real phone calls (optional)

Simulation mode needs nothing else — place a call from the patient page and submit a typed or mic reply in the browser.

For a real Plivo dial:

```bash
# Terminal A — public tunnel so Plivo can fetch answer XML + TTS audio
cloudflared tunnel --url http://localhost:8000
```

Then in `backend/.env`:

```env
TELEPHONY_MODE=plivo
PLIVO_AUTH_ID=MA…
PLIVO_AUTH_TOKEN=…
PLIVO_FROM_NUMBER=+91…
PUBLIC_BASE_URL=https://<your-tunnel>.trycloudflare.com
```

Restart the backend after changing env. Without a public `PUBLIC_BASE_URL`, Plivo cannot place the call.

---

## Demo path (≈3 minutes)

1. **Dashboard** — open escalations, adherence, upcoming schedule.
2. **Brain** — ask *"When should a follow-up call escalate?"* → cited answer from the seeded SOP. Ask something off-corpus → it refuses.
3. **Patients → Anita / Murugan** — open the care plan (meds + follow-up questions).
4. **Place call** — simulation: answer in the UI with text or mic. Try *"I have chest pain and I did not take my medicine"* → escalation + Care Graph events.
5. **Doctor reply** — type advice → automatic callback call in the patient's language; open escalations move to `ack`.

---

## Architecture

```
┌─────────────┐     REST / WS      ┌──────────────────────────────────────┐
│  React UI   │ ◄────────────────► │  FastAPI                             │
│  (Vite)     │                    │  patients · careplans · calls        │
└─────────────┘                    │  schedule · brain · documents        │
                                   │  analytics · plivo · voice WS        │
                                   └───────────┬──────────────────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    ▼                          ▼                          ▼
              ┌──────────┐              ┌────────────┐            ┌────────────┐
              │  SQLite  │              │   Sarvam   │            │   Plivo    │
              │ Care Graph│             │ LLM/STT/TTS│            │ voice IVR  │
              └──────────┘              └────────────┘            └────────────┘
```

```
backend/app/
├── main.py              lifespan: tables → seed → scheduler
├── models.py            patients, care plans, calls, turns, escalations, …
├── services/
│   ├── sarvam.py        chat, translate, TTS, STT, vision, analytics
│   ├── spoken.py        prescription shorthand → speakable TTS text
│   ├── brain.py         document tree index + cite-or-refuse Q&A
│   ├── careplus.py      scripts, reply understanding, schedule materialization
│   ├── telephony.py     Plivo dial + simulation
│   ├── scheduler.py     due slots → dial / retry / miss
│   └── voice/           streaming agent (browser) + turn dialogue / prewarm
└── routers/             HTTP + Plivo webhooks + voice WebSocket

frontend/src/
├── pages/               Dashboard, Patients, Brain, Documents, Settings
├── components/calls/    Call panel + simulation reply
└── i18n/                en · hi · ta · kn · mr
```

**Phone calls** are turn-based Plivo IVR (`/plivo/voice` → play question → record → STT → understand → next turn).  
**Browser voice console** uses the streaming WebSocket agent with VAD barge-in when `VOICE_MODE=stream`.

---

## Environment (backend)

| Variable | Purpose |
|---|---|
| `SARVAM_API_KEY` | Sarvam subscription key |
| `TELEPHONY_MODE` | `simulation` (default) or `plivo` |
| `PLIVO_*` | Auth ID, token, from-number |
| `PUBLIC_BASE_URL` | HTTPS URL Plivo can reach (tunnel in local dev) |
| `VOICE_MODE` | `stream` (browser agent) or `classic` |
| `DEFAULT_LANGUAGE` | e.g. `hi-IN` |
| `TIME_SCALE_DEMO` | Treat follow-up `ask_after_days` as minutes |
| `SEED_ON_STARTUP` | Seed demo patients / docs |

See [`backend/.env.example`](backend/.env.example) for the full list.

---

## API surface (highlights)

| Area | Endpoints |
|---|---|
| Patients | `GET/POST /patients`, timeline, recovered |
| Care plans | `GET/POST /patients/{id}/care-plan` |
| Calls | `POST /patients/{id}/call`, simulate-reply, doctor `reply` callback |
| Schedule | upcoming, rematerialize, run-now, snooze/skip |
| Brain | `POST /brain/ask` |
| Documents | upload / list / delete (PDF · image · md · txt) |
| Analytics | summary, escalations |
| Plivo | `/plivo/voice/{id}`, `/plivo/turn/{id}/{n}`, `/plivo/hangup/{id}` |
| Voice WS | `/ws/voice/browser/{call_id}` |

Full interactive docs: `/docs` when the backend is running.

---

## Project docs

| Doc | Contents |
|---|---|
| [`plan.md`](plan.md) / [`plan.html`](plan.html) | Full product & technical plan |
| [`progress_be.md`](progress_be.md) | Backend build status |
| [`progress_fe.md`](progress_fe.md) | Frontend build status |
| [`docs/realtime-voice.md`](docs/realtime-voice.md) | Streaming voice notes |

---

## License

Private / internal — not licensed for public redistribution unless stated otherwise.

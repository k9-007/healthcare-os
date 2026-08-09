# HealthcareOS — Full Technical Plan

**The AI Care Coordination Layer.** Two flagship modules, one closed loop:

- **Brain** — evidence-backed clinical knowledge from your own documents (every answer cited).
- **Patient Care+** — autonomous, multilingual voice follow-up + medication adherence.

Narrative: *trusted knowledge → autonomous multilingual voice engagement → structured data → doctor closes the loop → patient hears back in their own language.* This loop exercises almost the entire Sarvam stack.

> Diagrams below use **Mermaid**. GitHub/VS Code render them inline. For a fully rendered, styled version open **`plan.html`** in a browser.

---

## 1. Locked decisions

| Decision | Choice |
|---|---|
| Backend | **Python + FastAPI** |
| Frontend | **React + Vite + TypeScript + Tailwind** |
| Calls | **Real telephony via Twilio** (turn-based IVR) + **simulation fallback** |
| Scope | **Brain + Patient Care+ + Care Graph + light Analytics** |
| Languages | **Live multi-language** (Hindi, Tamil, Kannada, Marathi, English, …) |
| Storage | **SQLite** via SQLAlchemy (zero-infra, real persistence) |

---

## 2. The full Sarvam stack (grounded in the real API)

Base URL `https://api.sarvam.ai` · Auth header `api-subscription-key`.

| Capability | Endpoint | Model | Used by |
|---|---|---|---|
| Document intelligence (PDF/img → md/json, 23 langs, tables) | `/doc-digitization/job/v1` | Sarvam Vision (3B VLM) | Brain ingestion |
| Chat / reasoning / summaries | `/v1/chat/completions` | `sarvam-105b` | Brain answers, call-script gen, summaries |
| Text → speech (11 langs, 38 voices, base64 WAV) | `/text-to-speech` | `bulbul:v3` | Patient calls, callbacks |
| Speech → text (23 langs, auto-detect + confidence, 5 modes) | `/speech-to-text` | `saaras:v3` | Patient replies |
| Long audio (>30s) STT (diarization, timestamps) | `/speech-to-text/job/init` | `saaras:v3` | Long recordings |
| Structured extraction (typed Q&A) | `/text-analytics` | — | Transcript → symptom/adherence fields |
| Translation (EN ↔ 22 Indic) | `/translate` | `mayura:v1` / `sarvam-translate:v1` | Doctor↔patient language |
| Language identification | `/text-lid` | — | Routing / detection |
| Transliteration (script conversion) | `/transliterate` | — | Display aids, romanization |

**Sarvam TTS voices** (subset of 38): warm female `priya`,`neha`,`pooja`; professional male `aditya`,`rahul`,`kabir`; calm anchor `shreya`,`kavya`,`ritu`; authoritative `vijay`,`gokul`,`anand`; young energetic `tanya`,`suhani`,`niharika`.

**During build:** use the Sarvam **MCP** — `sarvam_code_*` (shapes, snippets, validate, recommend, speakers, pricing) at build-time; `sarvam_tools_*` (stt, tts, translate, vision_extract, text_analytics, llm_complete, identify_language, voice) to live-test before wiring into the backend.

---

## 3. High-level architecture

```mermaid
flowchart LR
  subgraph Client["React SPA (Vite+TS+Tailwind)"]
    UI_Dash["Dashboard / Analytics"]
    UI_Pat["Patient + Care Plan"]
    UI_Brain["Brain Chat"]
    UI_Graph["Care Graph"]
    UI_Call["Call Panel (record/playback)"]
  end

  subgraph API["FastAPI Backend"]
    R1["Routers: patients, careplans, documents, brain, calls, analytics"]
    S1["services/sarvam.py"]
    S2["services/brain.py"]
    S3["services/telephony.py"]
    S4["services/careplus.py"]
    DB[("SQLite via SQLAlchemy")]
    FS[["/data: audio + uploads"]]
  end

  subgraph Sarvam["Sarvam API (api.sarvam.ai)"]
    V["Vision /doc-digitization"]
    L["LLM /v1/chat/completions"]
    T["TTS /text-to-speech"]
    ST["STT /speech-to-text"]
    TA["Text Analytics"]
    TR["Translate"]
    LID["Text LID"]
  end

  Twilio["Twilio Voice (PSTN)"]
  Phone["Patient Phone"]

  Client -->|HTTP/JSON| R1
  R1 --> S1 & S2 & S3 & S4
  S2 --> DB
  S4 --> DB
  R1 --> DB
  S1 --> V & L & T & ST & TA & TR & LID
  S3 --> Twilio
  Twilio <-->|call audio| Phone
  Twilio -->|TwiML + recording webhooks| R1
  S1 --> FS
  Client -->|audio playback / mic| FS
```

---

## 4. Module flows

### 4.1 Brain — ingest & cited retrieval

```mermaid
flowchart TD
  A["Doctor uploads PDF/img<br/>(guideline, SOP, discharge, lab)"] --> B["POST /documents"]
  B --> C["Sarvam Vision job<br/>create → upload → start → poll"]
  C --> D["Extracted Markdown"]
  D --> E["Chunk + store DocChunk<br/>(page-aware)"]
  E --> F["Index (BM25/keyword)"]
  G["Doctor asks question<br/>POST /brain/ask"] --> H["Retrieve top-k chunks"]
  F --> H
  H --> I["sarvam-105b<br/>cite-or-refuse prompt"]
  I --> J["Answer + citations + confidence"]
  J --> K["UI renders answer w/ source snippets"]
```

### 4.2 Patient Care+ — autonomous voice follow-up

```mermaid
flowchart TD
  A["Doctor builds Care Plan<br/>meds + follow-up Qs + language"] --> B["Enable Patient Care+"]
  B --> C["sarvam-105b drafts call script"]
  C --> D["Translate → patient language"]
  D --> E["TTS bulbul:v3 → WAV"]
  E --> F{"TELEPHONY_MODE"}
  F -->|twilio| G["Twilio outbound call → IVR play/record"]
  F -->|simulation| H["Browser plays TTS + records mic"]
  G --> I["Patient reply audio"]
  H --> I
  I --> J["STT saaras:v3 → transcript (+lang, confidence)"]
  J --> K["Text Analytics → structured fields<br/>took_medicine? symptoms[] pain_score urgency"]
  K --> L["Persist ExtractedResponse + CallLog"]
  L --> M["Emit CareEvents"]
  M --> N{"urgency = high?"}
  N -->|yes| O["Create Escalation + nurse alert"]
  N -->|no| P["Update adherence + symptom trend"]
```

### 4.3 Care Graph — explainable journey

```mermaid
flowchart LR
  D["Discharge"] --> M["Medicine started"] --> C1["Day-2 call"]
  C1 --> MD["Missed dose"] --> AL["Doctor alert"]
  AL --> AD["Advice sent (callback)"] --> S["Symptoms improved"] --> R["Recovered"]
```

### 4.4 Analytics — hospital view

```mermaid
flowchart TD
  CE["CareEvents + CallLogs + ExtractedResponses"] --> AGG["GET /analytics/summary"]
  AGG --> T1["Adherence %"]
  AGG --> T2["Missed doses"]
  AGG --> T3["Patients at risk"]
  AGG --> T4["Escalations"]
  AGG --> T5["Follow-up completion"]
  AGG --> T6["Call success rate"]
```

---

## 5. User flow diagrams

### 5.1 Doctor journey

```mermaid
journey
  title Doctor journey
  section Setup
    Upload documents to Brain: 4: Doctor
    Create patient + care plan: 4: Doctor
  section Engage
    Enable Patient Care+: 5: Doctor
    Review call summaries: 4: Doctor
  section Decide
    Ask Brain (cited): 5: Doctor
    Send reply → auto callback: 5: Doctor
```

### 5.2 Patient call journey (turn-based)

```mermaid
sequenceDiagram
  actor P as Patient
  participant TW as Twilio
  participant API as FastAPI
  participant SV as Sarvam
  API->>SV: TTS(script, lang) → WAV
  API->>TW: place call (PUBLIC_BASE_URL webhooks)
  TW->>P: rings + <Play> TTS
  P-->>TW: speaks reply
  TW->>API: recording webhook (audio URL)
  API->>SV: STT(audio) → transcript+lang
  API->>SV: Text Analytics → structured fields
  API->>API: persist CallLog + events + escalation?
  API-->>P: (if reply queued) callback call
```

---

## 6. Closed-loop sequence (the demo backbone)

```mermaid
sequenceDiagram
  actor Doc as Doctor
  participant FE as React
  participant BE as FastAPI
  participant SV as Sarvam
  participant TW as Twilio
  actor Pat as Patient

  Doc->>FE: Upload discharge PDF
  FE->>BE: POST /documents
  BE->>SV: Vision extract → md
  BE-->>FE: indexed ✓

  Doc->>FE: Build care plan (meds, Qs, lang)
  FE->>BE: POST /patients/{id}/care-plan
  Doc->>FE: Enable Patient Care+
  FE->>BE: POST /patients/{id}/call
  BE->>SV: LLM script → Translate → TTS
  BE->>TW: outbound call
  TW->>Pat: Hindi voice call
  Pat-->>TW: reply (Hindi)
  TW->>BE: recording webhook
  BE->>SV: STT → Text Analytics
  BE-->>FE: transcript + structured + Care Graph update

  Doc->>FE: Ask Brain "missed meds this week?"
  FE->>BE: POST /brain/ask
  BE->>SV: retrieve + sarvam-105b (cited)
  BE-->>FE: answer + citations

  Doc->>FE: Reply "take after dinner"
  FE->>BE: doctor reply
  BE->>SV: Translate → TTS
  BE->>TW: callback call
  TW->>Pat: Hindi callback ✓
```

---

## 7. Database schema (ERD)

```mermaid
erDiagram
  PATIENT ||--o{ DOCUMENT : has
  PATIENT ||--o| CAREPLAN : has
  PATIENT ||--o{ CALLLOG : has
  PATIENT ||--o{ CAREEVENT : has
  PATIENT ||--o{ ESCALATION : has
  DOCUMENT ||--o{ DOCCHUNK : split_into
  CAREPLAN ||--o{ MEDICINE : contains
  CAREPLAN ||--o{ FOLLOWUPQUESTION : contains
  CALLLOG ||--o{ EXTRACTEDRESPONSE : yields
  CALLLOG ||--o| ESCALATION : may_raise
  FOLLOWUPQUESTION ||--o{ EXTRACTEDRESPONSE : answered_by

  PATIENT {
    int id PK
    string name
    string phone
    string preferred_language "BCP-47 e.g. hi-IN"
    string diagnosis
    string family_contact
    string notes
    datetime created_at
  }
  DOCUMENT {
    int id PK
    int patient_id FK "nullable (global docs)"
    string title
    string type "guideline|sop|discharge|lab|formulary"
    string file_path
    text extracted_md
    string status "pending|extracting|ready|failed"
    datetime created_at
  }
  DOCCHUNK {
    int id PK
    int document_id FK
    int ordinal
    int page
    text text
  }
  CAREPLAN {
    int id PK
    int patient_id FK
    string status "active|paused|done"
    datetime created_at
  }
  MEDICINE {
    int id PK
    int care_plan_id FK
    string name
    string dose
    string schedule "csv times e.g. 08:00,20:00"
  }
  FOLLOWUPQUESTION {
    int id PK
    int care_plan_id FK
    text text
    string type "boolean|number|enum|short"
    string options "csv for enum"
    int ask_after_days
  }
  CALLLOG {
    int id PK
    int patient_id FK
    string direction "outbound|inbound"
    string mode "twilio|simulation"
    string status "queued|ringing|completed|failed"
    text script_text
    string tts_audio_path
    string recording_path
    text transcript
    string detected_language
    float language_confidence
    datetime created_at
  }
  EXTRACTEDRESPONSE {
    int id PK
    int call_log_id FK
    int question_id FK "nullable"
    string key
    string value
    string value_type "boolean|number|enum|text"
  }
  CAREEVENT {
    int id PK
    int patient_id FK
    datetime ts
    string type "discharge|med_started|call|missed_dose|symptom|alert|advice|recovered"
    string title
    text detail
    string severity "info|warn|critical"
  }
  ESCALATION {
    int id PK
    int patient_id FK
    int call_log_id FK
    string reason
    string urgency "low|medium|high"
    string status "open|ack|closed"
    datetime created_at
  }
```

---

## 8. API surface & flows

### 8.1 Endpoint catalog

| Method | Path | Purpose | Sarvam used |
|---|---|---|---|
| POST | `/patients` | Create patient | — |
| GET | `/patients` / `/patients/{id}` | List / detail | — |
| POST | `/patients/{id}/care-plan` | Create/update care plan | — |
| GET | `/patients/{id}/care-plan` | Get care plan | — |
| POST | `/documents` | Upload → Vision extract → index | Vision |
| GET | `/documents` / `/documents/{id}` | List / detail | — |
| POST | `/brain/ask` | Cited Q&A | LLM (+retrieval) |
| POST | `/patients/{id}/call` | Script → translate → TTS → place call | LLM, Translate, TTS |
| POST | `/calls/{id}/simulate-reply` | Upload reply audio → STT → analytics | STT, Text Analytics |
| POST | `/patients/{id}/reply` | Doctor reply → translate → TTS callback | Translate, TTS |
| GET | `/patients/{id}/timeline` | Care Graph events | — |
| GET | `/analytics/summary` | Hospital KPIs | — |
| POST | `/twilio/voice/{call_id}` | Returns TwiML (`<Play>`+`<Record>`) | — |
| POST | `/twilio/recording/{call_id}` | Recording → STT → analytics | STT, Text Analytics |
| POST | `/twilio/status/{call_id}` | Call status updates | — |

### 8.2 Brain ask — request flow

```mermaid
sequenceDiagram
  participant FE as React
  participant BE as /brain/ask
  participant IDX as Chunk index
  participant LLM as sarvam-105b
  FE->>BE: { question }
  BE->>IDX: retrieve top-k (BM25)
  IDX-->>BE: chunks[] (doc,page,text)
  BE->>LLM: system=cite-or-refuse + context + question
  LLM-->>BE: answer (with [n] markers)
  BE-->>FE: { answer, citations[{doc,page,snippet}], confidence }
```

### 8.3 Document ingestion — Vision job pipeline

```mermaid
sequenceDiagram
  participant BE as /documents
  participant V as Sarvam Vision
  BE->>V: POST /doc-digitization/job/v1 {lang, format}
  V-->>BE: job_id, upload target
  BE->>V: PUT file to presigned URL
  BE->>V: POST /{job_id}/start
  loop poll
    BE->>V: GET /{job_id}/status
    V-->>BE: state
  end
  V-->>BE: Completed → output ZIP (md)
  BE->>BE: unzip, chunk, store DocChunk
```

---

## 9. Telephony design (Twilio, turn-based IVR)

```mermaid
sequenceDiagram
  participant BE as FastAPI
  participant TW as Twilio
  actor P as Patient
  BE->>TW: calls.create(to, from, url=PUBLIC/twilio/voice/{id})
  TW->>BE: POST /twilio/voice/{id}
  BE-->>TW: TwiML <Play>tts.wav</Play><Record action=/twilio/recording/{id}>
  TW->>P: play + record
  P-->>TW: audio
  TW->>BE: POST /twilio/recording/{id} (RecordingUrl)
  BE->>BE: download → Sarvam STT → Text Analytics → persist
  TW->>BE: POST /twilio/status/{id} (completed)
```

**Prereqs for real calls:** Twilio SID/token + voice number; **ngrok** (`brew install ngrok`, not yet installed) → set `PUBLIC_BASE_URL`. Trial accounts dial only verified numbers; +91 has regulatory limits → consider **Exotel** (India-native) as alternative. Default `TELEPHONY_MODE=simulation` for safe demos.

---

## 10. Repo layout

```
healthcare-os/
├── plan.md · plan.html · README.md
├── backend/
│   ├── requirements.txt · .env.example
│   └── app/
│       ├── main.py · config.py · db.py · models.py · schemas.py · seed.py
│       ├── services/  sarvam.py · brain.py · telephony.py · careplus.py
│       └── routers/   patients.py · careplans.py · documents.py · brain.py · calls.py · analytics.py
└── frontend/
    └── src/
        ├── api/client.ts · lib/languages.ts · lib/audio.ts
        ├── pages/ Dashboard · Patients · PatientDetail · Brain
        └── components/ CarePlanBuilder · CareGraph · CallPanel · BrainChat · Tiles
```

---

## 11. Phased build plan (~8–10 hrs)

| Phase | Work | Est. |
|---|---|---|
| 0 | Scaffold FastAPI + Vite React, config/.env, SQLite, CORS, static mounts | 45m |
| 1 | `services/sarvam.py` wrappers + smoke-test each via MCP | 1h |
| 2 | Brain: upload → Vision → chunk/index → cited Q&A + chat UI | 2h |
| 3 | Care+: profile + care-plan builder → script gen → TTS; STT → analytics → escalation | 2.5h |
| 4 | Telephony: Twilio place-call + TwiML + recording→STT; simulation mode | 1.5h |
| 5 | Care Graph timeline + Analytics dashboard | 1.5h |
| 6 | Close loop (reply → translate → TTS callback) + multi-language + polish | 1.5h |
| 7 | Seed demo data, dark clinical UI, README + demo script + Twilio/ngrok docs | 45m |

Core loop works end-to-end (simulation) by end of Phase 4; Twilio upgrades it to real calls.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Live call fails on stage | Simulation mode + pre-recorded reply clip |
| Vision job latency/timeout | Pre-ingest demo docs; allow text/markdown upload fallback |
| STT wrong language | Pass patient `preferred_language` hint; show detected lang + confidence |
| TTS 500-char/call limit | Chunk scripts; keep them short + natural |
| Rate limits mid-demo | Cache/pre-generate audio; retry w/ backoff |
| Scope creep | Freeze to Brain + Care+ + Care Graph + Analytics |

---

## 13. Demo script (3 min)

1. Upload discharge summary → Brain ingests (Vision).
2. Build care plan (Metformin + 3 Qs), language = Hindi → Enable Patient Care+.
3. Call fires → natural Hindi TTS (Bulbul + Translate).
4. Patient replies in Hindi → transcript + structured symptoms; switch a 2nd patient to Tamil for **live multi-language** (Saaras + Text Analytics + LID).
5. "Severe chest pain" reply → red escalation on dashboard + Care Graph.
6. Ask Brain "Any missed meds this week?" → cited answer.
7. Doctor replies → automated Hindi callback. **Loop closed.**

---

## 14. To start building

1. Approve plan → I scaffold Phase 0.
2. Twilio creds **or** start in **simulation** (wire Twilio later).
3. Sample clinical PDFs to seed Brain, or I generate realistic dummies.
4. Demo languages beyond Hindi (Tamil / Kannada / Marathi).

---

## 15. End-to-end journey — from patient to hospital

This is the full lifecycle of one patient (Mrs. Sharma, diabetic, Hindi-speaking) through HealthcareOS — and how **every feature and Sarvam capability** is exercised, from bedside to hospital boardroom.

### 15.1 The journey at a glance (swimlanes)

```mermaid
flowchart TB
  subgraph PT["👤 Patient (Mrs. Sharma)"]
    P1["Discharged after treatment"]
    P2["Receives voice call in Hindi"]
    P3["Answers: took medicine / symptoms"]
    P4["Reports severe chest pain one day"]
    P5["Gets doctor's advice as a call"]
    P6["Recovers"]
  end
  subgraph HOS["🏥 HealthcareOS"]
    H1["Vision extracts discharge summary → Brain"]
    H2["Care plan → LLM call script → Translate → TTS"]
    H3["Twilio/sim call → STT → Text Analytics"]
    H4["Structured data → Care Graph + adherence"]
    H5["Urgency classifier → Escalation"]
    H6["Doctor reply → Translate → TTS callback"]
    H7["Aggregate KPIs → Analytics"]
  end
  subgraph DR["🩺 Doctor / Nurse"]
    D1["Builds care plan + follow-up questions"]
    D2["Reviews call summaries + Care Graph"]
    D3["Asks Brain (cited answers)"]
    D4["Gets escalation alert, responds"]
  end
  subgraph ADM["📊 Hospital Admin"]
    A1["Adherence, risk, escalations, recovery trends"]
    A2["Population-level insight → ops decisions"]
  end

  P1 --> H1 --> D1 --> H2 --> P2 --> P3 --> H3 --> H4 --> D2
  P4 --> H3
  H4 --> H5 --> D4 --> H6 --> P5 --> P6
  D2 --> D3
  H4 --> H7 --> A1 --> A2
```

### 15.2 Stage-by-stage — every feature mapped

| # | Stage | Actor | HealthcareOS feature / module | Sarvam capability | Output |
|---|---|---|---|---|---|
| 1 | **Admission & discharge** | Hospital | Document upload → **Brain** ingestion | **Vision** (`/doc-digitization`) | Discharge summary + labs → searchable, cited knowledge |
| 2 | **Knowledge grounding** | Doctor | **Brain** indexes SOPs, guidelines, formularies | Vision + chunk/index | Hospital-specific evidence base |
| 3 | **Care plan design** | Doctor | **Care Plan builder** (meds, schedule, follow-up Qs, language) | — | Structured plan tied to patient's `preferred_language` |
| 4 | **Activation** | HealthcareOS | **Enable Patient Care+** → call-script generation | **LLM** `sarvam-105b` | Warm, clinically-safe script |
| 5 | **Localization** | HealthcareOS | Script → patient's language | **Translate** (`mayura`/`sarvam-translate`) | Hindi/Tamil/… script |
| 6 | **Voice call** | Patient | Autonomous **medicine call** | **TTS** `bulbul:v3` + **Twilio** IVR | Natural spoken call ("time for your Metformin") |
| 7 | **Listening** | Patient | Patient answers by voice | **STT** `saaras:v3` (auto-detect + confidence) | Transcript + detected language |
| 8 | **Understanding** | HealthcareOS | Turn transcript → structured fields | **Text Analytics** (typed Q&A) | took_medicine?, symptoms[], pain_score, urgency |
| 9 | **Adherence tracking** | HealthcareOS | Missed-dose detection + adherence score | — | Adherence %, missed-dose events, caregiver notify |
| 10 | **Symptom follow-up** | Patient | Dynamic follow-up questions (doctor-authored) | TTS + STT + Text Analytics | Symptom timeline (nausea, swelling, fever…) |
| 11 | **Care Graph** | Doctor | Visual **explainable journey** | — | Discharge → med → call → missed → alert → advice → recovered |
| 12 | **Escalation** | Nurse | Urgency classification of "severe chest pain" | LLM + Text Analytics | Red alert, nurse notified, emergency guidance |
| 13 | **Doctor Q&A** | Doctor | **Brain** query over transcripts + plan | LLM (cite-or-refuse) | "Missed 2 doses this week" — with citations |
| 14 | **Closed loop** | Patient | Doctor reply → automated callback | Translate + TTS + Twilio | "Doctor advises: take after dinner" (in Hindi) |
| 15 | **Multi-language** | Any patient | Same loop in Tamil/Kannada/Marathi live | LID + STT + Translate + TTS | One platform, many languages |
| 16 | **Recovery** | Patient | Journey marked complete | — | `recovered` CareEvent closes the graph |
| 17 | **Hospital intelligence** | Admin | **Analytics** dashboard | — (aggregates) | Adherence %, at-risk patients, escalations, recovery trend, call success rate |

### 15.3 Value delivered at each level

```mermaid
flowchart LR
  subgraph Patient
    PV["Care in their own language<br/>No app, no literacy barrier<br/>Feels heard, safer at home"]
  end
  subgraph Doctor
    DV["No manual follow-up calls<br/>Structured data, not raw audio<br/>Cited, trustworthy answers"]
  end
  subgraph Hospital
    HV["Higher adherence + fewer readmissions<br/>Early risk detection<br/>Population-level insight"]
  end
  PV --> DV --> HV
```

### 15.4 One sentence for the judges

> A patient is discharged, and from that moment HealthcareOS **reads their records (Vision)**, **plans and speaks to them in their language (LLM + Translate + TTS)**, **listens and understands their replies (STT + Text Analytics)**, **draws their recovery as an explainable Care Graph**, **escalates danger instantly**, **lets the doctor answer with cited evidence (Brain)**, **closes the loop with an automated callback**, and **rolls it all up into hospital-wide intelligence** — the entire arc from a single bedside to the hospital boardroom, powered end-to-end by Sarvam.

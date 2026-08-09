# Frontend progress — HealthcareOS

_Last updated: Aug 9, 2026_

## Status at a glance

| Phase (plan.md §16.9) | Scope | Status |
|---|---|---|
| F0 | Vite + TS + Tailwind scaffold, AppShell, router, theme, api client | ✅ Done |
| F1 | Patients list + New Patient dialog + Patient Detail shell | ✅ Done |
| F2 | CarePlanBuilder (medicines, follow-up questions, call window) + Enable Care+ | ✅ Done |
| F3 | CallPanel (TTS playback, mic recording, transcript, structured fields) | ✅ Done |
| F4 | Care Graph animated timeline | ✅ Done |
| F5 | Brain chat with citations + confidence + Documents upload | ✅ Done |
| F6 | Dashboard KPIs, adherence chart, escalations, scheduler queue | ✅ Done |
| F7 | i18n (i18next), full multi-language UI strings | ✅ Done — en/hi/ta/kn/mr tables, persisted switcher |

`src/api/client.ts` now talks to the FastAPI backend (`VITE_API_BASE`, default `http://localhost:8000`), mapping snake_case responses to the typed camelCase models in one place.

## Tech stack (as built)

- **React 19 + Vite 8 + TypeScript** (strict), **Tailwind CSS v4** (CSS-first `@theme` tokens)
- **React Router v7** · **TanStack Query v5** (server state, invalidation on mutations) · **Zustand + persist** (UI language, telephony mode) · **i18next / react-i18next** (UI strings)
- **Recharts** (adherence trend) · **framer-motion** (Care Graph + dialog animations) · **lucide-react** (icons)
- Deviation from plan: **no shadcn/ui** — hand-built primitives (`Button`, `Dialog`, `Field`, `Tag`, `AdherenceBar`, …) for a leaner bundle and a distinctive, non-generic look. Forms use controlled state + light inline validation instead of react-hook-form/zod (can be added when forms grow).

## Design system

- **Dual theme (dark default + light)**: all color tokens are runtime CSS variables (`@theme inline` → `:root` / `html.light`), toggled from the top bar (sun/moon), persisted in localStorage, applied pre-paint by an inline script in `index.html` so there's no flash. Chart colors are picked per-theme in JS since Recharts sets SVG attributes that can't resolve CSS vars.
- Dark clinical theme per §16.2: near-black navy canvas `#090e1a`, panels `#0d1424`, hairline borders, desaturated teal accent `#45d0c0`, status colors good/warn/crit as specified. Escalations pulse red. Light theme mirrors it: `#f2f5fa` canvas, white panels, navy text, deeper teal `#0f9a8c` and darkened status colors for contrast.
- Typography: Inter (UI) with **tabular numerals** for all metrics, Instrument Serif for the wordmark/Brain headline, JetBrains Mono available for code-ish values.
- Dense, data-first layout: small-caps micro-labels, tight tables, no gradient cards or emoji — deliberately avoids the "AI-generated dashboard" look.
- Accessibility: focus rings everywhere, ARIA labels on icon buttons and dialogs, Escape-to-close, large-enough tap targets.

## What's built, screen by screen

- **AppShell** — sidebar (nav + live escalation badge + scheduler status), top bar (patient search with results dropdown, global language switcher), content area.
- **Dashboard** — 6 KPI tiles (adherence + delta, missed doses, at-risk, escalations, follow-up completion, call success), 14-day adherence area chart, escalation feed with acknowledge action and red pulse on open/high items, **Upcoming calls scheduler queue** showing due time, per-call medicine targets and language (§17.8).
- **Patients** — filterable table (all / active / at-risk / recovered), adherence bars, risk badges, next-call countdown; **New Patient dialog** with validation, language picker; navigates to the new record on create.
- **Patient Detail** — header (identity, language incl. native script, family contact, adherence) + 4 tabs:
  - *Overview*: current medicines with schedules, follow-up questions, latest AI call summary, recent activity.
  - *Care plan*: **CarePlanBuilder** — add/edit/remove medicines (name, dose, times, instructions) and typed follow-up questions (yes-no / number / choice / free text, ask-on-day, at-time), call window, Save (with "schedule rematerialized" feedback) and **Enable/Pause Patient Care+**.
  - *Calls*: **CallPanel** hero flow — place call → script shown + spoken via browser TTS (stand-in for Sarvam bulbul) → record patient reply with the real microphone (MediaRecorder) or use a sample reply → STT/analytics stage → transcript (English + native script), detected language + confidence, structured fields. Plus **DoctorReplyBox** (closed loop: reply → voice callback appears in history and timeline) and full call history.
  - *Care graph*: animated vertical timeline, discharge → recovery, severity-colored icon nodes, critical events pulse.
- **Brain** — chat with suggested questions, reasoning indicator, answers with numbered **expandable citations** (doc + page + snippet), confidence bar, and a visible **cite-or-refuse** refusal state when no grounding exists.
- **Documents** — drag-drop / click upload (goes through pending → extracting → indexed states), document grid with type, pages, size, extraction status and markdown excerpt preview.
- **Settings** — telephony mode toggle (simulation ⇄ Twilio, with credential fields), scheduler knobs (tick interval, call window, retries, backoff), Sarvam API key placeholders.

## i18n (F7)

- **i18next + react-i18next**, initialized in `src/i18n/` with typed string tables in `src/i18n/locales/` — the `en` table defines the `Translation` shape, so `tsc` catches any locale with missing keys.
- **5 full locales: English, Hindi, Tamil, Kannada, Marathi** (~140 strings each: nav, page titles, dashboard KPIs/tables, patients list + registration dialog, patient record tabs, care-plan builder, call panel state machine, doctor reply box, Brain, Documents, Settings). Telugu, Bengali, Gujarati, Punjabi and Malayalam fall back to English until their tables are written.
- Top-bar switcher drives `i18n.changeLanguage` + `document.documentElement.lang` through the Zustand store; the choice **persists in localStorage** and survives reloads.
- Interpolated strings (`{{count}}`, `{{name}}`, `{{language}}`, `{{pct}}`) used for scheduler status, Care+ notes, detected-language lines, confidence scores.
- Patient data (diagnoses, transcripts, medicine names) intentionally stays as backend data — translated by the backend/Sarvam, not by UI string tables.
- Sidebar branding updated: **"HealthcareOS — The AI Care Coordination Layer for Bharat"** (tagline localized in every language table).

## Mock data & realism

- 6 patients across Hindi, Tamil, Kannada, Marathi, English; timestamps derived from "now" so the demo never looks stale.
- Real-feeling clinical narrative: a CHF patient (weight gain + edema → escalation → doctor reply → Tamil callback) that exercises the entire closed loop from the plan's demo script (§13).
- Transcripts include native-script renderings; structured extraction fields are tone-coded (good/warn/crit).

## Verified

- `npm run build` passes clean (tsc strict + Vite).
- Every screen loaded and screenshot-checked in the browser; full call-panel loop (place → speak → reply → analyze) and Brain Q&A exercised end to end.
- i18n verified in the browser: dashboard + patient record rendered in Hindi and Tamil, language persisted across a full page reload, English fallback confirmed.
- Light theme verified in the browser: dashboard + patient record screenshot-checked in both themes, toggle persists across reloads, dark theme visually unchanged after the token refactor.

## Known gaps / next steps

1. **Remaining locale tables** — Telugu, Bengali, Gujarati, Punjabi, Malayalam currently fall back to English; add tables in `src/i18n/locales/` as needed.
2. **Twilio mode polling** — CallPanel currently simulation-only; add `GET /calls/:id` polling when backend lands.
3. Code-split routes (bundle is ~980 KB raw / 295 KB gzip, mostly Recharts) — optional polish.
4. react-hook-form + zod if care-plan validation needs to grow beyond the current inline checks.

## How to run

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

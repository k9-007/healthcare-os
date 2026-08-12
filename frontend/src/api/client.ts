// API client for the FastAPI backend (plan.md §8). Every method keeps the
// signature the UI components were built against; the raw snake_case backend
// shapes are mapped to the camelCase types in ./types here, in one place.

import type {
  AnalyticsSummary, BrainAnswer, CallLog, CareEvent, CarePlan, Escalation,
  FollowUpQuestion, HospitalDoc, Medicine, NewPatientInput, Patient,
  PrescriptionParseResult, ScheduledCall, StructuredField,
} from './types'

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const get = <T>(path: string) => request<T>(path)
const send = <T>(path: string, method: string, body?: unknown) =>
  request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

/** Backend datetimes are naive UTC — mark them as UTC so Date parses correctly. */
function utc(dt: string): string {
  if (!dt) return dt
  return /Z$|[+-]\d\d:?\d\d$/.test(dt) ? dt : `${dt}Z`
}

// ---------- backend (snake_case) shapes ----------

interface BePatient {
  id: number; name: string; age: number; sex: string; phone: string
  preferred_language: string; timezone: string; diagnosis: string
  family_contact: string; notes: string; created_at: string
  adherence_pct?: number | null; risk?: string; status?: string
  next_call_at?: string | null
}

interface BeMedicine {
  id: number; name: string; dose: string; schedule: string
  instructions: string; start_date: string | null; end_date: string | null
}

interface BeQuestion {
  id: number; text: string; type: FollowUpQuestion['type']; options: string
  ask_after_days: number; at_time: string
}

interface BeCarePlan {
  id: number; patient_id: number; status: 'active' | 'paused' | 'done'
  start_date: string; call_window: string
  medicines: BeMedicine[]; questions: BeQuestion[]
}

interface BeResponse {
  id: number; question_id: number | null; medicine_id: number | null
  key: string; value: string; value_type: string
}

interface BeCall {
  id: number; patient_id: number; direction: CallLog['direction']
  mode: CallLog['mode']; status: string; kind: string
  script_text: string; script_text_translated: string
  tts_audio_path: string; transcript: string; transcript_english: string
  detected_language: string; language_confidence: number
  created_at: string; responses: BeResponse[]
}

interface BeCallCreate { call: BeCall; tts_audio_url: string | null; escalation_id: number | null }
interface BeReplyProcess { call: BeCall; escalation_id: number | null; events_created: number }

interface BeEscalation {
  id: number; patient_id: number; reason: string
  urgency: Escalation['urgency']; status: Escalation['status']
  created_at: string; patient_name: string | null
}

interface BeScheduledCall {
  id: number; patient_id: number; kind: ScheduledCall['kind']; due_at: string
  status: ScheduledCall['status']; targets: { label: string }[]
  patient_name: string | null; language: string | null
}

interface BeDocument {
  id: number; title: string; type: HospitalDoc['type']; status: HospitalDoc['status']
  created_at: string; pages: number; size_kb: number; excerpt: string
  patient_id: number | null
}

interface BeAnalytics {
  total_patients: number; active_care_plans: number; adherence_pct: number
  missed_doses: number; patients_at_risk: number; open_escalations: number
  followup_completion_pct: number; call_success_rate_pct: number; total_calls: number
  adherence_trend: { date: string; adherence_pct: number | null; responses: number }[]
  recent_escalations: BeEscalation[]
}

interface BeBrainAnswer {
  answer: string; refused: boolean; confidence: number
  citations: { document_title: string; page: number; snippet: string }[]
}

// ---------- mappers ----------

function mapPatient(p: BePatient): Patient {
  return {
    id: p.id,
    name: p.name,
    age: p.age ?? 0,
    sex: p.sex === 'M' ? 'M' : 'F',
    phone: p.phone,
    preferredLanguage: p.preferred_language,
    timezone: p.timezone,
    diagnosis: p.diagnosis,
    dischargedOn: utc(p.created_at),
    adherence: Math.round(p.adherence_pct ?? 100),
    risk: (p.risk ?? 'low') as Patient['risk'],
    status: p.status === 'recovered' ? 'recovered' : 'active',
    nextCallAt: p.next_call_at ? utc(p.next_call_at) : null,
    familyContact: p.family_contact || undefined,
    notes: p.notes || undefined,
  }
}

function mapPlan(c: BeCarePlan): CarePlan {
  return {
    id: c.id,
    patientId: c.patient_id,
    status: c.status,
    startDate: c.start_date,
    callWindow: c.call_window,
    carePlusEnabled: c.status === 'active',
    medicines: c.medicines.map((m): Medicine => ({
      id: m.id,
      name: m.name,
      dose: m.dose,
      schedule: m.schedule.split(',').map((s) => s.trim()).filter(Boolean),
      instructions: m.instructions,
      startDate: m.start_date ?? '',
      endDate: m.end_date ?? '',
    })),
    questions: c.questions.map((q): FollowUpQuestion => ({
      id: q.id,
      text: q.text,
      type: q.type,
      options: q.options ? q.options.split(',').map((o) => o.trim()).filter(Boolean) : undefined,
      askAfterDays: q.ask_after_days,
      atTime: q.at_time,
    })),
  }
}

function mapResponse(r: BeResponse): StructuredField {
  let label = r.key.replace(/_/g, ' ')
  label = label.charAt(0).toUpperCase() + label.slice(1)
  let value = r.value
  let tone: StructuredField['tone'] = 'neutral'

  if (r.key === 'took_medicine') {
    label = 'Took medicine'
    value = r.value === 'true' ? 'Yes' : r.value === 'false' ? 'No' : 'Unclear'
    tone = r.value === 'true' ? 'good' : r.value === 'false' ? 'crit' : 'neutral'
  } else if (r.key === 'urgency') {
    value = r.value.charAt(0).toUpperCase() + r.value.slice(1)
    tone = r.value === 'high' ? 'crit' : r.value === 'medium' ? 'warn' : 'good'
  } else if (r.key === 'symptom') {
    tone = 'warn'
  } else if (r.key === 'pain_score') {
    const n = Number(r.value)
    tone = n >= 7 ? 'crit' : n >= 4 ? 'warn' : 'good'
  } else if (r.value_type === 'boolean') {
    value = r.value === 'true' ? 'Yes' : r.value === 'false' ? 'No' : r.value
  }

  return { key: `${r.key}#${r.id}`, label, value, tone }
}

function mapCall(c: BeCall, escalated?: boolean): CallLog {
  const english = c.transcript_english || c.transcript
  return {
    id: c.id,
    patientId: c.patient_id,
    direction: c.direction,
    mode: c.mode,
    status: c.status as CallLog['status'],
    kind: (c.kind === 'manual' ? 'medicine' : c.kind) as CallLog['kind'],
    placedAt: utc(c.created_at),
    durationSec: 0, // backend doesn't track duration yet
    scriptText: c.script_text_translated || c.script_text,
    transcript: english,
    transcriptNative: c.transcript && c.transcript !== english ? c.transcript : undefined,
    detectedLanguage: c.detected_language || '',
    languageConfidence: c.language_confidence,
    structured: c.responses.map(mapResponse),
    escalated: escalated ?? c.responses.some((r) => r.key === 'urgency' && r.value === 'high'),
    ttsAudioUrl: c.tts_audio_path ? `${API_BASE}/data/${c.tts_audio_path}` : null,
  }
}

function mapEscalation(e: BeEscalation): Escalation {
  return {
    id: e.id,
    patientId: e.patient_id,
    patientName: e.patient_name ?? `Patient #${e.patient_id}`,
    reason: e.reason,
    urgency: e.urgency,
    status: e.status,
    createdAt: utc(e.created_at),
  }
}

// Brain history lives client-side for the session; the backend is stateless Q&A.
let brainId = 0
const brainHistory: BrainAnswer[] = []

const SAMPLE_REPLY =
  'Yes, I have taken the medicine just now, after food. I am feeling alright today.'

export const api = {
  async listPatients(): Promise<Patient[]> {
    const rows = await get<BePatient[]>('/patients')
    return rows.map(mapPatient)
  },

  async getPatient(id: number): Promise<Patient> {
    return mapPatient(await get<BePatient>(`/patients/${id}`))
  },

  async createPatient(input: NewPatientInput): Promise<Patient> {
    const created = await send<BePatient>('/patients', 'POST', {
      name: input.name,
      age: input.age,
      sex: input.sex,
      phone: input.phone,
      preferred_language: input.preferredLanguage,
      diagnosis: input.diagnosis,
      family_contact: input.familyContact ?? '',
    })
    return mapPatient(created)
  },

  async getCarePlan(patientId: number): Promise<CarePlan | null> {
    try {
      return mapPlan(await get<BeCarePlan>(`/patients/${patientId}/care-plan`))
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },

  async saveCarePlan(plan: CarePlan): Promise<CarePlan> {
    const saved = await send<BeCarePlan>(`/patients/${plan.patientId}/care-plan`, 'POST', {
      status: plan.carePlusEnabled ? 'active' : plan.status === 'done' ? 'done' : 'paused',
      start_date: plan.startDate ? plan.startDate.slice(0, 10) : undefined,
      call_window: plan.callWindow,
      medicines: plan.medicines.map((m) => ({
        name: m.name,
        dose: m.dose,
        schedule: m.schedule.filter(Boolean).join(',') || '08:00',
        instructions: m.instructions,
        start_date: m.startDate ? m.startDate.slice(0, 10) : null,
        end_date: m.endDate ? m.endDate.slice(0, 10) : null,
      })),
      questions: plan.questions.map((q) => ({
        text: q.text,
        type: q.type,
        options: (q.options ?? []).join(','),
        ask_after_days: q.askAfterDays,
        at_time: q.atTime,
      })),
    })
    return mapPlan(saved)
  },

  async listCalls(patientId?: number): Promise<CallLog[]> {
    if (patientId === undefined) return []
    const rows = await get<BeCall[]>(`/patients/${patientId}/calls`)
    return rows.map((c) => mapCall(c))
  },

  async placeCall(patientId: number): Promise<CallLog> {
    const res = await send<BeCallCreate>(`/patients/${patientId}/call`, 'POST')
    return mapCall(res.call, res.escalation_id != null)
  },

  async submitReply(callId: number, audio?: Blob): Promise<CallLog> {
    const form = new FormData()
    if (audio) form.append('audio', audio, 'reply.webm')
    else form.append('text', SAMPLE_REPLY)
    const res = await request<BeReplyProcess>(`/calls/${callId}/simulate-reply`, {
      method: 'POST',
      body: form,
    })
    return mapCall(res.call, res.escalation_id != null)
  },

  async sendDoctorReply(patientId: number, message: string): Promise<CallLog> {
    const res = await send<BeCallCreate>(`/patients/${patientId}/reply`, 'POST', { message })
    return mapCall(res.call)
  },

  async getTimeline(patientId: number): Promise<CareEvent[]> {
    const rows = await get<(Omit<CareEvent, 'patientId'> & { patient_id: number })[]>(
      `/patients/${patientId}/timeline`,
    )
    return rows
      .map((e) => ({
        id: e.id,
        patientId: e.patient_id,
        ts: utc(e.ts),
        type: e.type,
        title: e.title,
        detail: e.detail,
        severity: e.severity,
      }))
      .sort((a, b) => b.ts.localeCompare(a.ts))
  },

  async listEscalations(): Promise<Escalation[]> {
    const rows = await get<BeEscalation[]>('/escalations')
    return rows.map(mapEscalation)
  },

  async ackEscalation(id: number): Promise<void> {
    await send(`/escalations/${id}?status=ack`, 'PATCH')
  },

  async listUpcomingCalls(): Promise<ScheduledCall[]> {
    const rows = await get<BeScheduledCall[]>('/schedule/upcoming?limit=25')
    return rows.map((sc) => ({
      id: sc.id,
      patientId: sc.patient_id,
      patientName: sc.patient_name ?? `Patient #${sc.patient_id}`,
      kind: sc.kind,
      dueAt: utc(sc.due_at),
      targets: sc.targets.map((t) => t.label),
      status: sc.status,
      language: sc.language ?? '',
    }))
  },

  async getAnalytics(): Promise<AnalyticsSummary> {
    const a = await get<BeAnalytics>('/analytics/summary')
    const overall = Math.round(a.adherence_pct)
    const trend = a.adherence_trend.map((t) => ({
      day: new Date(`${t.date}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short' }),
      adherence: t.adherence_pct !== null ? Math.round(t.adherence_pct) : overall,
      calls: t.responses,
    }))
    const known = a.adherence_trend.filter((t) => t.adherence_pct !== null)
    const delta = known.length >= 2
      ? Math.round((known[known.length - 1].adherence_pct! - known[0].adherence_pct!) * 10) / 10
      : 0
    return {
      adherence: overall,
      adherenceDelta: delta,
      missedDoses7d: a.missed_doses,
      patientsAtRisk: a.patients_at_risk,
      openEscalations: a.open_escalations,
      followupCompletion: Math.round(a.followup_completion_pct),
      callSuccessRate: Math.round(a.call_success_rate_pct),
      activePatients: a.active_care_plans,
      trend,
      languageMix: [],
    }
  },

  async listDocuments(): Promise<HospitalDoc[]> {
    const rows = await get<BeDocument[]>('/documents')
    return rows.map(mapDocument)
  },

  async uploadDocument(file: File, patientId?: number | null): Promise<HospitalDoc> {
    const form = new FormData()
    form.append('file', file)
    form.append('title', file.name.replace(/\.[^.]+$/, ''))
    form.append('type', patientId != null ? 'discharge' : 'guideline')
    if (patientId != null) form.append('patient_id', String(patientId))
    const d = await request<BeDocument>('/documents', { method: 'POST', body: form })
    return mapDocument(d)
  },

  async parsePrescription(file: File, patientId?: number | null): Promise<PrescriptionParseResult> {
    const form = new FormData()
    form.append('file', file)
    form.append('language', 'en-IN')
    form.append('persist', 'true')
    if (patientId != null) form.append('patient_id', String(patientId))
    const r = await request<{
      status: PrescriptionParseResult['status']
      document_id: number | null
      medicines: Array<{
        name: string; matched_name: string; generic_name: string; brand_name: string
        dose: string; frequency: string; schedule: string; duration: string
        instructions: string; confidence: number; match_score: number
        raw_name: string; matched: boolean
      }>
      unmatched: string[]
      warnings: string[]
      error: string
    }>('/prescriptions/parse', { method: 'POST', body: form })
    return {
      status: r.status,
      documentId: r.document_id,
      unmatched: r.unmatched ?? [],
      warnings: r.warnings ?? [],
      error: r.error ?? '',
      medicines: (r.medicines ?? []).map((m) => ({
        name: m.name,
        matchedName: m.matched_name,
        genericName: m.generic_name,
        brandName: m.brand_name,
        dose: m.dose,
        frequency: m.frequency,
        schedule: m.schedule.split(',').map((s) => s.trim()).filter(Boolean),
        duration: m.duration,
        instructions: m.instructions,
        confidence: m.confidence,
        matchScore: m.match_score,
        rawName: m.raw_name,
        matched: m.matched,
      })),
    }
  },

  async getBrainHistory(): Promise<BrainAnswer[]> {
    return [...brainHistory]
  },

  async askBrain(question: string, patientId?: number | null, patientName?: string): Promise<BrainAnswer> {
    const res = await send<BeBrainAnswer>('/brain/ask', 'POST', {
      question,
      patient_id: patientId ?? null,
    })
    const answer: BrainAnswer = {
      id: ++brainId,
      question,
      answer: res.answer,
      refused: res.refused,
      citations: res.citations.map((c) => ({
        doc: c.document_title,
        page: c.page,
        snippet: c.snippet,
      })),
      confidence: res.confidence,
      answeredAt: new Date().toISOString(),
      patientId: patientId ?? null,
      patientName,
    }
    brainHistory.push(answer)
    return answer
  },
}

function mapDocument(d: BeDocument): HospitalDoc {
  return {
    id: d.id,
    title: d.title,
    type: d.type,
    pages: d.pages,
    sizeKb: d.size_kb,
    status: d.status,
    uploadedAt: utc(d.created_at),
    excerpt: d.excerpt || undefined,
    patientId: d.patient_id,
  }
}

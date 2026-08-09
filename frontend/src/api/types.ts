export type Risk = 'low' | 'medium' | 'high'
export type Urgency = 'low' | 'medium' | 'high'
export type Severity = 'info' | 'warn' | 'critical'

export interface Patient {
  id: number
  name: string
  age: number
  sex: 'F' | 'M'
  phone: string
  preferredLanguage: string // BCP-47, e.g. hi-IN
  timezone: string
  diagnosis: string
  dischargedOn: string // ISO date
  adherence: number // 0..100
  risk: Risk
  status: 'active' | 'recovered'
  nextCallAt: string | null
  familyContact?: string
  notes?: string
}

export interface Medicine {
  id: number
  name: string
  dose: string
  schedule: string[] // clock times, e.g. ["08:00","20:00"]
  instructions: string
  startDate: string
  endDate: string
}

export interface FollowUpQuestion {
  id: number
  text: string
  type: 'boolean' | 'number' | 'enum' | 'short'
  options?: string[]
  askAfterDays: number
  atTime: string
}

export interface CarePlan {
  id: number
  patientId: number
  status: 'active' | 'paused' | 'done'
  startDate: string
  callWindow: string // "08:00-20:00"
  carePlusEnabled: boolean
  medicines: Medicine[]
  questions: FollowUpQuestion[]
}

export interface StructuredField {
  key: string
  label: string
  value: string
  tone: 'good' | 'warn' | 'crit' | 'neutral'
}

export interface CallLog {
  id: number
  patientId: number
  direction: 'outbound' | 'inbound'
  mode: 'plivo' | 'simulation'
  status: 'queued' | 'ringing' | 'in-progress' | 'completed' | 'failed'
  kind: 'medicine' | 'followup' | 'callback'
  placedAt: string
  durationSec: number
  scriptText: string
  transcript: string
  transcriptNative?: string
  detectedLanguage: string
  languageConfidence: number
  structured: StructuredField[]
  escalated: boolean
  /** Sarvam TTS audio served by the backend — null when TTS is unavailable. */
  ttsAudioUrl?: string | null
}

export interface CareEvent {
  id: number
  patientId: number
  ts: string
  type: 'discharge' | 'med_started' | 'call' | 'missed_dose' | 'symptom' | 'alert' | 'advice' | 'recovered'
  title: string
  detail: string
  severity: Severity
}

export interface Escalation {
  id: number
  patientId: number
  patientName: string
  reason: string
  urgency: Urgency
  status: 'open' | 'ack' | 'closed'
  createdAt: string
}

export interface ScheduledCall {
  id: number
  patientId: number
  patientName: string
  kind: 'medicine' | 'followup' | 'callback'
  dueAt: string
  targets: string[]
  status: 'pending' | 'placed' | 'completed' | 'failed' | 'skipped' | 'no_answer'
  language: string
}

export interface HospitalDoc {
  id: number
  title: string
  type: 'guideline' | 'sop' | 'discharge' | 'lab' | 'formulary'
  pages: number
  sizeKb: number
  status: 'pending' | 'extracting' | 'ready' | 'failed'
  uploadedAt: string
  excerpt?: string
}

export interface Citation {
  doc: string
  page: number
  snippet: string
}

export interface BrainAnswer {
  id: number
  question: string
  answer: string
  refused: boolean
  citations: Citation[]
  confidence: number // 0..1
  answeredAt: string
}

export interface AnalyticsSummary {
  adherence: number
  adherenceDelta: number
  missedDoses7d: number
  patientsAtRisk: number
  openEscalations: number
  followupCompletion: number
  callSuccessRate: number
  activePatients: number
  trend: { day: string; adherence: number; calls: number }[]
  languageMix: { language: string; patients: number }[]
}

export interface NewPatientInput {
  name: string
  age: number
  sex: 'F' | 'M'
  phone: string
  preferredLanguage: string
  diagnosis: string
  familyContact?: string
}

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Mic, PhoneCall, Square, Volume2 } from 'lucide-react'
import type { CallLog, Patient } from '@/api/types'
import { usePlaceCall, useSubmitReply } from '@/api/hooks'
import { languageLabel } from '@/lib/languages'
import { useAppStore } from '@/store/useAppStore'
import { Button, cx, SectionHeader, Tag } from '@/components/ui/primitives'

type Stage = 'idle' | 'placing' | 'speaking' | 'listening' | 'recording' | 'processing' | 'done'

export function CallPanel({ patient }: { patient: Patient }) {
  const { t } = useTranslation()
  const [stage, setStage] = useState<Stage>('idle')
  const [activeCall, setActiveCall] = useState<CallLog | null>(null)
  const [micError, setMicError] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const { telephonyMode } = useAppStore()

  const placeCall = usePlaceCall(patient.id)
  const submitReply = useSubmitReply()

  useEffect(() => () => window.speechSynthesis?.cancel(), [])

  async function onPlaceCall() {
    setStage('placing')
    setMicError(null)
    const call = await placeCall.mutateAsync()
    setActiveCall(call)
    setStage('speaking')

    // Browser fallback when Sarvam TTS audio is unavailable, so the demo stays audible.
    const speakFallback = () => {
      if ('speechSynthesis' in window) {
        const utter = new SpeechSynthesisUtterance(call.scriptText)
        utter.lang = patient.preferredLanguage
        utter.rate = 0.95
        utter.onend = () => setStage('listening')
        utter.onerror = () => setStage('listening')
        window.speechSynthesis.speak(utter)
      } else {
        setTimeout(() => setStage('listening'), 2500)
      }
    }

    if (call.ttsAudioUrl) {
      const audio = new Audio(call.ttsAudioUrl)
      audio.onended = () => setStage('listening')
      audio.onerror = speakFallback
      audio.play().catch(speakFallback)
    } else {
      speakFallback()
    }
  }

  async function startRecording() {
    setMicError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => chunksRef.current.push(e.data)
      rec.onstop = async () => {
        stream.getTracks().forEach((tr) => tr.stop())
        setStage('processing')
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        const updated = await submitReply.mutateAsync({ callId: activeCall!.id, audio: blob })
        setActiveCall(updated)
        setStage('done')
      }
      recorderRef.current = rec
      rec.start()
      setStage('recording')
    } catch {
      setMicError(t('calls.micError'))
    }
  }

  async function skipRecording() {
    setStage('processing')
    const updated = await submitReply.mutateAsync({ callId: activeCall!.id })
    setActiveCall(updated)
    setStage('done')
  }

  const busy = stage === 'placing' || stage === 'processing'

  const statusText: Record<Stage, string> = {
    idle: t('calls.statusIdle'),
    placing: t('calls.statusPlacing'),
    speaking: t('calls.statusSpeaking', { language: languageLabel(patient.preferredLanguage) }),
    listening: t('calls.statusListening'),
    recording: t('calls.statusRecording'),
    processing: t('calls.statusProcessing'),
    done: t('calls.statusDone'),
  }

  return (
    <section className="panel p-4">
      <SectionHeader
        title={t('calls.panelTitle')}
        aside={<Tag tone={telephonyMode === 'twilio' ? 'accent' : 'neutral'}>{t('calls.mode', { mode: telephonyMode })}</Tag>}
      />

      {/* status line */}
      <div className="mb-4 flex items-center gap-2 text-xs">
        <span
          className={cx(
            'size-1.5 rounded-full',
            stage === 'idle' && 'bg-faint',
            (stage === 'placing' || stage === 'processing') && 'bg-warn animate-pulse',
            (stage === 'speaking' || stage === 'listening' || stage === 'recording') && 'bg-accent animate-pulse',
            stage === 'done' && 'bg-good',
          )}
        />
        <span className="text-fog">{statusText[stage]}</span>
      </div>

      {activeCall && (
        <div className="mb-4 rounded-md border border-line bg-canvas/60 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-mist">
            <Volume2 size={11} /> {t('calls.scriptLabel')}
          </div>
          <p className="text-[13px] leading-relaxed text-fog">{activeCall.scriptText}</p>
        </div>
      )}

      {stage === 'done' && activeCall && activeCall.transcript && (
        <div className="mb-4 rounded-md border border-good/25 bg-good/5 p-3">
          <div className="mb-1.5 text-[11px] text-mist">{t('calls.replyLabel')}</div>
          <p className="text-[13px] leading-relaxed text-fog">“{activeCall.transcript}”</p>
          {activeCall.transcriptNative && <p className="mt-1 text-xs text-mist">{activeCall.transcriptNative}</p>}
          <p className="num mt-2 text-[11px] text-mist">
            {t('calls.detected', {
              language: languageLabel(activeCall.detectedLanguage),
              pct: (activeCall.languageConfidence * 100).toFixed(0),
            })}
          </p>
        </div>
      )}

      {micError && <p className="mb-3 text-xs text-warn">{micError}</p>}

      <div className="flex flex-wrap gap-2">
        {(stage === 'idle' || stage === 'done') && (
          <Button variant="primary" onClick={onPlaceCall} disabled={busy}>
            <PhoneCall size={14} /> {stage === 'done' ? t('calls.callAgain') : t('calls.callNow')}
          </Button>
        )}
        {(stage === 'listening' || stage === 'speaking') && (
          <>
            <Button variant="primary" onClick={startRecording} disabled={stage === 'speaking'}>
              <Mic size={14} /> {t('calls.record')}
            </Button>
            <Button onClick={skipRecording} disabled={stage === 'speaking'}>
              {t('calls.sample')}
            </Button>
          </>
        )}
        {stage === 'recording' && (
          <Button variant="danger" onClick={() => recorderRef.current?.stop()}>
            <Square size={13} /> {t('calls.stop')}
          </Button>
        )}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-faint">{t('calls.footNote')}</p>
    </section>
  )
}

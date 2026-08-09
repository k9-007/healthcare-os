import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpenCheck, CornerDownLeft, ShieldAlert, UserRound } from 'lucide-react'
import type { BrainAnswer } from '@/api/types'
import { useAskBrain, useBrainHistory, usePatients } from '@/api/hooks'
import { timeAgo } from '@/lib/format'
import { Button, cx, Skeleton } from '@/components/ui/primitives'
import { ScopePicker } from '@/components/ScopePicker'

const SUGGESTIONS = [
  'When should a follow-up call escalate?',
  'What should Anita Sharma do if she misses a Metformin dose?',
  'How do we manage hypoglycemia symptoms?',
  'What are the warning signs that need immediate care?',
]

export function Brain() {
  const { t } = useTranslation()
  const { data: history } = useBrainHistory()
  const { data: patients } = usePatients()
  const ask = useAskBrain()
  const [question, setQuestion] = useState('')
  const [scopeId, setScopeId] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const scopeName = scopeId != null ? patients?.find((p) => p.id === scopeId)?.name : undefined

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history?.length, ask.isPending])

  function onAsk(q?: string) {
    const text = (q ?? question).trim()
    if (text.length < 3 || ask.isPending) return
    setQuestion('')
    ask.mutate({ question: text, patientId: scopeId, patientName: scopeName })
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-7.5rem)] max-w-3xl flex-col">
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto pb-6 pr-1">
        <div className="pt-2 text-center">
          <p className="font-serif text-xl text-bright">{t('brain.headline')}</p>
          <p className="mt-1 text-xs text-mist">{t('brain.subtitle')}</p>
        </div>

        {!history ? (
          <Skeleton className="h-40" />
        ) : (
          history.map((a) => <AnswerBlock key={a.id} a={a} />)
        )}

        {ask.isPending && (
          <div className="space-y-2">
            <QuestionRow
              text={ask.variables?.question ?? ''}
              patientName={ask.variables?.patientName}
              patientId={ask.variables?.patientId}
            />
            <div className="flex items-center gap-2 pl-1 text-xs text-mist">
              <span className="size-1.5 animate-pulse rounded-full bg-violet" />
              {t('brain.thinking')}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-line pt-4">
        {(history?.length ?? 0) < 3 && (
          <div className="mb-3 flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => onAsk(s)}
                className="rounded-full border border-line-strong px-3 py-1 text-[11.5px] text-mist transition-colors hover:border-accent/40 hover:text-fog focus-ring cursor-pointer"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <ScopePicker
            value={scopeId ?? 'general'}
            onChange={(v) => setScopeId(typeof v === 'number' ? v : null)}
            patients={patients}
            staticOptions={[
              { value: 'general', label: t('brain.scopeGeneral', { defaultValue: 'General' }), icon: 'building' },
            ]}
            direction="up"
            triggerClassName="h-10"
          />
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onAsk()}
            placeholder={
              scopeName
                ? t('brain.placeholderPatient', { defaultValue: 'Ask about {{name}} — their docs + hospital knowledge…', name: scopeName })
                : t('brain.placeholder')
            }
            className="h-10 flex-1 rounded-lg border border-line-strong bg-panel px-3.5 text-[13px] text-bright placeholder:text-faint focus-ring"
          />
          <Button variant="primary" className="h-10 px-4" onClick={() => onAsk()} disabled={ask.isPending || question.trim().length < 3}>
            <CornerDownLeft size={14} /> {t('brain.ask')}
          </Button>
        </div>
      </div>
    </div>
  )
}

/** Minimal markdown renderer for Brain answers: paragraphs, "- " bullets,
 *  "**bold**" values, "⚠️" warning bullets and inline [n] citation chips. */
function AnswerBody({ text }: { text: string }) {
  const lines = text.split('\n').map((l) => l.trim())
  const blocks: { type: 'p' | 'ul'; lines: string[] }[] = []
  for (const line of lines) {
    if (!line) continue
    const isBullet = /^[-•]\s+/.test(line)
    const last = blocks[blocks.length - 1]
    if (isBullet) {
      const item = line.replace(/^[-•]\s+/, '')
      if (last?.type === 'ul') last.lines.push(item)
      else blocks.push({ type: 'ul', lines: [item] })
    } else {
      blocks.push({ type: 'p', lines: [line] })
    }
  }
  return (
    <div className="space-y-2.5 text-[13.5px] leading-relaxed text-fog">
      {blocks.map((b, i) =>
        b.type === 'p' ? (
          <p key={i} className={i === 0 ? 'text-[14px] text-bright' : undefined}>
            <Inline text={b.lines[0]} />
          </p>
        ) : (
          <ul key={i} className="space-y-1.5">
            {b.lines.map((item, j) => {
              const warning = item.startsWith('⚠')
              return (
                <li key={j} className="flex gap-2">
                  <span
                    className={cx(
                      'mt-[7px] size-1 shrink-0 rounded-full',
                      warning ? 'bg-warn' : 'bg-violet/60',
                    )}
                  />
                  <span className={warning ? 'text-warn' : undefined}>
                    <Inline text={item.replace(/^⚠\uFE0F?\s*/, '')} warning={warning} />
                  </span>
                </li>
              )
            })}
          </ul>
        ),
      )}
    </div>
  )
}

/** Inline renderer: **bold** + [n] citation chips. */
function Inline({ text, warning }: { text: string; warning?: boolean }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\[\d+\])/g).filter(Boolean)
  return (
    <>
      {warning && <span className="mr-1">⚠️</span>}
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong key={i} className="font-semibold text-bright">
              {part.slice(2, -2)}
            </strong>
          )
        }
        const cite = part.match(/^\[(\d+)\]$/)
        if (cite) {
          return (
            <sup
              key={i}
              title={`Source ${cite[1]}`}
              className="num mx-0.5 inline-grid size-3.5 translate-y-[-1px] place-items-center rounded bg-violet/15 text-[9px] font-bold text-violet"
            >
              {cite[1]}
            </sup>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}

function QuestionRow({ text, patientName, patientId }: { text: string; patientName?: string; patientId?: number | null }) {
  return (
    <div className="flex flex-col items-end gap-1">
      {patientName && (
        <span className="flex items-center gap-1 rounded-full border border-violet/30 bg-violet/10 px-2 py-0.5 text-[10px] font-medium text-violet">
          <UserRound size={9} /> {patientName}
          {patientId != null && <span className="num opacity-70">#{patientId}</span>}
        </span>
      )}
      <p className="max-w-[80%] rounded-lg rounded-br-sm bg-raised px-3.5 py-2 text-[13px] text-bright">
        {text}
      </p>
    </div>
  )
}

function AnswerBlock({ a }: { a: BrainAnswer }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2.5">
      <QuestionRow text={a.question} patientName={a.patientName} patientId={a.patientId} />
      <div
        className={cx(
          'rounded-lg rounded-tl-sm border p-4',
          a.refused ? 'border-warn/30 bg-warn/4' : 'border-line bg-panel',
        )}
      >
        {a.refused && (
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-warn">
            <ShieldAlert size={13} /> {t('brain.refused')}
          </div>
        )}
        <AnswerBody text={a.answer} />

        {a.citations.length > 0 && (
          <div className="mt-3.5 space-y-2 border-t border-line pt-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
              {t('brain.sources', { defaultValue: 'Sources' })}
            </p>
            {a.citations.map((c, i) => (
              <details key={i} className="group">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-mist hover:text-fog">
                  <span className="num grid size-4.5 shrink-0 place-items-center rounded bg-violet/12 text-[10px] font-semibold text-violet">
                    {i + 1}
                  </span>
                  <BookOpenCheck size={12} className="shrink-0" />
                  <span className="truncate text-fog">{c.doc}</span>
                  <span className="num shrink-0">p.{c.page}</span>
                </summary>
                <blockquote className="ml-6.5 mt-1.5 border-l-2 border-violet/30 pl-3 text-xs italic leading-relaxed text-mist">
                  {c.snippet}
                </blockquote>
              </details>
            ))}
          </div>
        )}

        <div className="mt-3 flex items-center justify-between">
          {!a.refused ? (
            <span className="flex items-center gap-2">
              <span className="h-1 w-24 overflow-hidden rounded-full bg-line">
                <span
                  className={cx('block h-full rounded-full', a.confidence > 0.85 ? 'bg-good' : 'bg-warn')}
                  style={{ width: `${a.confidence * 100}%` }}
                />
              </span>
              <span className="num text-[11px] text-mist">{t('brain.confidence', { pct: (a.confidence * 100).toFixed(0) })}</span>
            </span>
          ) : (
            <span />
          )}
          <span className="num text-[11px] text-faint">{timeAgo(a.answeredAt)}</span>
        </div>
      </div>
    </div>
  )
}

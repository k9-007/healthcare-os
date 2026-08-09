import { useTranslation } from 'react-i18next'
import { PhoneIncoming, PhoneOutgoing } from 'lucide-react'
import type { CallLog } from '@/api/types'
import { languageLabel } from '@/lib/languages'
import { callDuration, timeAgo } from '@/lib/format'
import { cx, Tag } from '@/components/ui/primitives'

export function CallSummaryCard({ call }: { call: CallLog }) {
  const { t } = useTranslation()

  return (
    <article className={cx('panel p-4', call.escalated && 'border-crit/40')}>
      <div className="flex items-center gap-2 text-xs text-mist">
        {call.direction === 'outbound' ? <PhoneOutgoing size={12} /> : <PhoneIncoming size={12} />}
        <span>{t(`kind.${call.kind}`)}</span>
        <span>·</span>
        <span className="num">{timeAgo(call.placedAt)}</span>
        <span>·</span>
        <span className="num">{callDuration(call.durationSec)}</span>
        <span className="ml-auto flex items-center gap-1.5">
          <Tag tone="neutral">{call.mode}</Tag>
          {call.escalated && <Tag tone="crit">{t('calls.escalated')}</Tag>}
        </span>
      </div>

      {call.transcript && (
        <blockquote className="mt-3 border-l-2 border-line-strong pl-3 text-[13px] leading-relaxed text-fog">
          “{call.transcript}”
          {call.transcriptNative && (
            <span className="mt-1 block text-xs text-mist">{call.transcriptNative}</span>
          )}
        </blockquote>
      )}

      {call.languageConfidence > 0 && (
        <p className="num mt-2 text-[11px] text-mist">
          {t('calls.detected', {
            language: languageLabel(call.detectedLanguage),
            pct: (call.languageConfidence * 100).toFixed(0),
          })}
        </p>
      )}

      {call.structured.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
          {call.structured.map((f) => (
            <div key={f.key} className="flex items-baseline justify-between gap-2 border-b border-line/50 pb-1.5">
              <dt className="text-[11px] text-mist">{f.label}</dt>
              <dd
                className={cx(
                  'text-xs font-medium',
                  f.tone === 'good' && 'text-good',
                  f.tone === 'warn' && 'text-warn',
                  f.tone === 'crit' && 'text-crit',
                  f.tone === 'neutral' && 'text-fog',
                )}
              >
                {f.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </article>
  )
}

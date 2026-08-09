import { useTranslation } from 'react-i18next'
import type { CallLog, CareEvent, CarePlan, Patient } from '@/api/types'
import { timeAgo } from '@/lib/format'
import { EmptyState, SectionHeader, Skeleton, Tag } from '@/components/ui/primitives'
import { CallSummaryCard } from '@/components/calls/CallSummaryCard'

interface Props {
  patient: Patient
  plan: CarePlan | null
  calls?: CallLog[]
  timeline?: CareEvent[]
}

export function Overview({ plan, calls, timeline }: Props) {
  const { t } = useTranslation()
  const latestCall = calls?.find((c) => c.status === 'completed')
  const recentEvents = timeline?.slice(0, 4)

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <section className="panel p-4">
        <SectionHeader title={t('overview.currentMeds')} />
        {!plan ? (
          <EmptyState title={t('overview.noPlan')} hint={t('overview.noPlanHint')} />
        ) : (
          <ul className="divide-y divide-line/60">
            {plan.medicines.map((m) => (
              <li key={m.id} className="flex items-baseline justify-between gap-3 py-2.5">
                <div>
                  <span className="font-medium text-bright">{m.name}</span>
                  <span className="num ml-2 text-xs text-fog">{m.dose}</span>
                  {m.instructions && m.instructions !== '—' && (
                    <span className="ml-2 text-xs text-mist">{m.instructions.toLowerCase()}</span>
                  )}
                </div>
                <span className="num text-xs text-mist">{m.schedule.join(' · ')}</span>
              </li>
            ))}
          </ul>
        )}
        {plan && plan.questions.length > 0 && (
          <>
            <SectionHeader title={t('overview.followupQs')} aside={<span className="text-[11px] text-faint">{t('overview.askedOnCalls')}</span>} />
            <ul className="space-y-1.5">
              {plan.questions.map((q) => (
                <li key={q.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-fog">{q.text}</span>
                  <span className="num shrink-0 text-mist">{t('overview.day', { n: q.askAfterDays })} · {q.atTime}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <div className="space-y-5">
        <section>
          <SectionHeader title={t('overview.latestSummary')} />
          {calls === undefined ? (
            <Skeleton className="h-44" />
          ) : latestCall ? (
            <CallSummaryCard call={latestCall} />
          ) : (
            <div className="panel">
              <EmptyState title={t('overview.noCalls')} hint={t('overview.noCallsHint')} />
            </div>
          )}
        </section>

        <section className="panel p-4">
          <SectionHeader title={t('overview.recentActivity')} />
          {!recentEvents ? (
            <Skeleton className="h-24" />
          ) : recentEvents.length === 0 ? (
            <EmptyState title={t('overview.emptyActivity')} />
          ) : (
            <ul className="space-y-2">
              {recentEvents.map((e) => (
                <li key={e.id} className="flex items-baseline gap-2.5 text-xs">
                  <span className="num w-16 shrink-0 text-faint">{timeAgo(e.ts)}</span>
                  <Tag tone={e.severity === 'critical' ? 'crit' : e.severity === 'warn' ? 'warn' : 'neutral'}>
                    {e.type.replace('_', ' ')}
                  </Tag>
                  <span className="text-fog">{e.title}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}

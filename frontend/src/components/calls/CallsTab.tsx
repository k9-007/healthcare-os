import { useTranslation } from 'react-i18next'
import type { CallLog, Patient } from '@/api/types'
import { EmptyState, SectionHeader, Skeleton } from '@/components/ui/primitives'
import { CallPanel } from './CallPanel'
import { CallSummaryCard } from './CallSummaryCard'
import { DoctorReplyBox } from './DoctorReplyBox'

export function CallsTab({ patient, calls }: { patient: Patient; calls?: CallLog[] }) {
  const { t } = useTranslation()

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-5">
        <CallPanel patient={patient} />
        <DoctorReplyBox patient={patient} />
      </div>

      <section>
        <SectionHeader title={t('calls.history')} />
        {calls === undefined ? (
          <Skeleton className="h-60" />
        ) : calls.length === 0 ? (
          <div className="panel">
            <EmptyState title={t('calls.emptyHistory')} hint={t('calls.emptyHistoryHint')} />
          </div>
        ) : (
          <div className="space-y-3">
            {calls.map((c) => (
              <CallSummaryCard key={c.id} call={c} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

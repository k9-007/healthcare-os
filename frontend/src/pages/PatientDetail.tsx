import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from 'lucide-react'
import { useCalls, useCarePlan, usePatient, useTimeline } from '@/api/hooks'
import { cx, Skeleton } from '@/components/ui/primitives'
import { PatientHeader } from '@/components/patients/PatientHeader'
import { Overview } from '@/components/patients/Overview'
import { CarePlanBuilder } from '@/components/care/CarePlanBuilder'
import { CallsTab } from '@/components/calls/CallsTab'
import { CareGraph } from '@/components/graph/CareGraph'

const TABS = [
  { id: 'overview', key: 'detail.tabOverview' },
  { id: 'care-plan', key: 'detail.tabCarePlan' },
  { id: 'calls', key: 'detail.tabCalls' },
  { id: 'care-graph', key: 'detail.tabCareGraph' },
] as const
type Tab = (typeof TABS)[number]['id']

export function PatientDetail() {
  const { t } = useTranslation()
  const { id } = useParams()
  const patientId = Number(id)
  const [tab, setTab] = useState<Tab>('overview')

  const { data: patient, isLoading } = usePatient(patientId)
  const { data: plan } = useCarePlan(patientId)
  const { data: calls } = useCalls(patientId)
  const { data: timeline } = useTimeline(patientId)

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-72" />
      </div>
    )
  }

  if (!patient) {
    return (
      <div className="py-16 text-center">
        <p className="text-fog">{t('detail.notFound')}</p>
        <Link to="/patients" className="mt-2 inline-block text-sm text-accent hover:underline">
          {t('detail.back')}
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <Link to="/patients" className="inline-flex items-center gap-1.5 text-xs text-mist hover:text-fog">
        <ArrowLeft size={13} /> {t('detail.allPatients')}
      </Link>

      <PatientHeader patient={patient} plan={plan ?? null} />

      <div className="flex gap-0.5 border-b border-line">
        {TABS.map(({ id: tabId, key }) => (
          <button
            key={tabId}
            onClick={() => setTab(tabId)}
            className={cx(
              '-mb-px border-b-2 px-3.5 py-2 text-[13px] transition-colors focus-ring cursor-pointer',
              tab === tabId
                ? 'border-accent font-medium text-bright'
                : 'border-transparent text-mist hover:text-fog',
            )}
          >
            {t(key)}
            {tabId === 'calls' && calls && <span className="num ml-1.5 text-[11px] text-faint">{calls.length}</span>}
          </button>
        ))}
      </div>

      {tab === 'overview' && <Overview patient={patient} plan={plan ?? null} calls={calls} timeline={timeline} />}
      {tab === 'care-plan' && <CarePlanBuilder patient={patient} plan={plan ?? null} />}
      {tab === 'calls' && <CallsTab patient={patient} calls={calls} />}
      {tab === 'care-graph' && <CareGraph events={timeline} />}
    </div>
  )
}

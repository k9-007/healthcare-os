import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { usePatients } from '@/api/hooks'
import { languageLabel } from '@/lib/languages'
import { shortDate, timeUntil } from '@/lib/format'
import { AdherenceBar, Button, cx, EmptyState, RiskBadge, Skeleton } from '@/components/ui/primitives'
import { NewPatientDialog } from '@/components/patients/NewPatientDialog'

type Filter = 'all' | 'active' | 'at-risk' | 'recovered'

export function Patients() {
  const { t } = useTranslation()
  const { data: patients, isLoading } = usePatients()
  const [filter, setFilter] = useState<Filter>('all')
  const [dialogOpen, setDialogOpen] = useState(false)
  const navigate = useNavigate()

  const rows = useMemo(() => {
    if (!patients) return []
    switch (filter) {
      case 'active': return patients.filter((p) => p.status === 'active')
      case 'at-risk': return patients.filter((p) => p.risk !== 'low' && p.status === 'active')
      case 'recovered': return patients.filter((p) => p.status === 'recovered')
      default: return patients
    }
  }, [patients, filter])

  const filters: Array<{ id: Filter; label: string }> = [
    { id: 'all', label: t('patients.filterAll') },
    { id: 'active', label: t('patients.filterActive') },
    { id: 'at-risk', label: t('patients.filterAtRisk') },
    { id: 'recovered', label: t('patients.filterRecovered') },
  ]

  const cols = [
    t('patients.colPatient'),
    t('patients.colDiagnosis'),
    t('patients.colLanguage'),
    t('patients.colDischarged'),
    t('patients.colAdherence'),
    t('patients.colStatus'),
    t('patients.colNextCall'),
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex gap-1">
          {filters.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={cx(
                'rounded-md px-2.5 py-1.5 text-xs transition-colors focus-ring cursor-pointer',
                filter === f.id ? 'bg-raised text-bright font-medium' : 'text-mist hover:text-fog',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <Button variant="primary" onClick={() => setDialogOpen(true)}>
          <Plus size={14} /> {t('patients.newPatient')}
        </Button>
      </div>

      <div className="panel overflow-hidden">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState title={t('patients.empty')} hint={t('patients.emptyHint')} />
        ) : (
          <table className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-line bg-raised/40">
                {cols.map((h) => (
                  <th key={h} className="label-caps px-4 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => navigate(`/patients/${p.id}`)}
                  className="cursor-pointer border-b border-line/60 transition-colors last:border-0 hover:bg-hover/60"
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-bright">{p.name}</div>
                    <div className="num text-[11px] text-mist">{p.age} · {p.sex} · {p.phone}</div>
                  </td>
                  <td className="max-w-52 truncate px-4 py-3 text-fog">{p.diagnosis}</td>
                  <td className="px-4 py-3 text-fog">{languageLabel(p.preferredLanguage)}</td>
                  <td className="num px-4 py-3 text-mist">{shortDate(p.dischargedOn)}</td>
                  <td className="px-4 py-3"><AdherenceBar value={p.adherence} /></td>
                  <td className="px-4 py-3">
                    {p.status === 'recovered'
                      ? <span className="text-[11px] text-mist">{t('patients.recovered')}</span>
                      : <RiskBadge risk={p.risk} />}
                  </td>
                  <td className="num px-4 py-3 text-mist">{p.nextCallAt ? timeUntil(p.nextCallAt) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <NewPatientDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </div>
  )
}

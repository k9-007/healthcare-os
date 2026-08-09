import { useTranslation } from 'react-i18next'
import { CalendarDays, Phone, Users } from 'lucide-react'
import type { CarePlan, Patient } from '@/api/types'
import { languageLabel, languageNative } from '@/lib/languages'
import { shortDate, initials } from '@/lib/format'
import { AdherenceBar, RiskBadge, Tag } from '@/components/ui/primitives'

export function PatientHeader({ patient, plan }: { patient: Patient; plan: CarePlan | null }) {
  const { t } = useTranslation()

  return (
    <header className="panel flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
      <div className="flex items-center gap-3.5">
        <div className="grid size-11 place-items-center rounded-full border border-line-strong bg-raised font-serif text-[15px] text-accent">
          {initials(patient.name)}
        </div>
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-[17px] font-semibold tracking-tight text-bright">{patient.name}</h1>
            {patient.status === 'recovered'
              ? <Tag tone="good">{t('patients.recovered')}</Tag>
              : <RiskBadge risk={patient.risk} />}
            {plan?.carePlusEnabled && <Tag tone="accent">{t('detail.carePlusOn')}</Tag>}
          </div>
          <p className="mt-0.5 text-xs text-mist">
            {patient.age} · {patient.sex === 'F' ? t('newPatient.female') : t('newPatient.male')} · {patient.diagnosis}
          </p>
        </div>
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
        <span className="flex items-center gap-1.5 text-fog">
          <Phone size={12} className="text-mist" /> <span className="num">{patient.phone}</span>
        </span>
        <span className="flex items-center gap-1.5 text-fog">
          <CalendarDays size={12} className="text-mist" /> {t('detail.discharged', { date: shortDate(patient.dischargedOn) })}
        </span>
        {patient.familyContact && (
          <span className="flex items-center gap-1.5 text-fog">
            <Users size={12} className="text-mist" /> {patient.familyContact}
          </span>
        )}
        <span className="text-fog">
          {t('detail.speaks')} <span className="text-bright">{languageLabel(patient.preferredLanguage)}</span>
          <span className="ml-1 text-mist">{languageNative(patient.preferredLanguage)}</span>
        </span>
        <AdherenceBar value={patient.adherence} />
      </div>
    </header>
  )
}

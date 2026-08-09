import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useCreatePatient } from '@/api/hooks'
import { LANGUAGES } from '@/lib/languages'
import { Dialog } from '@/components/ui/Dialog'
import { Button, Field, Input, Select } from '@/components/ui/primitives'

export function NewPatientDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const create = useCreatePatient()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    const fd = new FormData(e.currentTarget)
    const name = String(fd.get('name') ?? '').trim()
    const phone = String(fd.get('phone') ?? '').trim()
    const age = Number(fd.get('age'))
    if (name.length < 3) return setError(t('newPatient.errName'))
    if (!/^[+\d][\d\s-]{8,}$/.test(phone)) return setError(t('newPatient.errPhone'))
    if (!age || age < 1 || age > 120) return setError(t('newPatient.errAge'))

    const patient = await create.mutateAsync({
      name,
      phone,
      age,
      sex: fd.get('sex') === 'M' ? 'M' : 'F',
      preferredLanguage: String(fd.get('language')),
      diagnosis: String(fd.get('diagnosis') ?? '').trim() || 'Pending intake',
      familyContact: String(fd.get('family') ?? '').trim() || undefined,
    })
    onClose()
    navigate(`/patients/${patient.id}`)
  }

  return (
    <Dialog open={open} onClose={onClose} title={t('newPatient.title')}>
      <form onSubmit={onSubmit} className="space-y-3.5">
        <Field label={t('newPatient.name')}>
          <Input name="name" placeholder={t('newPatient.namePlaceholder')} autoFocus />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label={t('newPatient.age')}>
            <Input name="age" type="number" min={1} max={120} placeholder="58" />
          </Field>
          <Field label={t('newPatient.sex')}>
            <Select name="sex" defaultValue="F">
              <option value="F">{t('newPatient.female')}</option>
              <option value="M">{t('newPatient.male')}</option>
            </Select>
          </Field>
          <Field label={t('newPatient.callLanguage')}>
            <Select name="language" defaultValue="hi-IN">
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>{l.label} · {l.native}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label={t('newPatient.phone')} hint={t('newPatient.phoneHint')}>
          <Input name="phone" placeholder="+91 98xxx xxxxx" />
        </Field>
        <Field label={t('newPatient.diagnosis')}>
          <Input name="diagnosis" placeholder={t('newPatient.diagnosisPlaceholder')} />
        </Field>
        <Field label={t('newPatient.family')}>
          <Input name="family" placeholder={t('newPatient.familyPlaceholder')} />
        </Field>

        {error && <p className="text-xs text-crit">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" onClick={onClose}>{t('newPatient.cancel')}</Button>
          <Button type="submit" variant="primary" disabled={create.isPending}>
            {create.isPending ? t('newPatient.submitting') : t('newPatient.submit')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

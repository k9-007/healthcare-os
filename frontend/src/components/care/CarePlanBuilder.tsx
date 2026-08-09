import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import type { CarePlan, FollowUpQuestion, Medicine, Patient } from '@/api/types'
import { useSaveCarePlan } from '@/api/hooks'
import { languageLabel } from '@/lib/languages'
import { Button, cx, Field, Input, SectionHeader, Select } from '@/components/ui/primitives'

let tmpId = -1

function blankPlan(patientId: number): CarePlan {
  return {
    id: tmpId--,
    patientId,
    status: 'active',
    startDate: new Date().toISOString(),
    callWindow: '08:00-20:00',
    carePlusEnabled: false,
    medicines: [],
    questions: [],
  }
}

export function CarePlanBuilder({ patient, plan }: { patient: Patient; plan: CarePlan | null }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<CarePlan>(plan ?? blankPlan(patient.id))
  const [dirty, setDirty] = useState(false)
  const [savedAt, setSavedAt] = useState<Date | null>(null)
  const save = useSaveCarePlan()

  useEffect(() => {
    if (plan) setDraft(plan)
  }, [plan])

  function update(patch: Partial<CarePlan>) {
    setDraft((d) => ({ ...d, ...patch }))
    setDirty(true)
  }

  function updateMedicine(id: number, patch: Partial<Medicine>) {
    update({ medicines: draft.medicines.map((m) => (m.id === id ? { ...m, ...patch } : m)) })
  }

  function updateQuestion(id: number, patch: Partial<FollowUpQuestion>) {
    update({ questions: draft.questions.map((q) => (q.id === id ? { ...q, ...patch } : q)) })
  }

  async function onSave(enableCarePlus?: boolean) {
    const next = enableCarePlus === undefined ? draft : { ...draft, carePlusEnabled: enableCarePlus }
    const saved = await save.mutateAsync(next)
    setDraft(saved)
    setDirty(false)
    setSavedAt(new Date())
  }

  return (
    <div className="space-y-5">
      {/* Medicines */}
      <section className="panel p-4">
        <SectionHeader
          title={t('carePlan.medicines')}
          aside={
            <Button
              size="sm"
              onClick={() =>
                update({
                  medicines: [
                    ...draft.medicines,
                    {
                      id: tmpId--, name: '', dose: '', schedule: ['08:00'],
                      instructions: '', startDate: new Date().toISOString(),
                      endDate: new Date(Date.now() + 14 * 864e5).toISOString(),
                    },
                  ],
                })
              }
            >
              <Plus size={13} /> {t('carePlan.addMedicine')}
            </Button>
          }
        />
        {draft.medicines.length === 0 ? (
          <p className="py-6 text-center text-xs text-mist">{t('carePlan.noMeds')}</p>
        ) : (
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_90px_150px_1fr_28px] gap-2 px-1">
              {[t('carePlan.colName'), t('carePlan.colDose'), t('carePlan.colTimes'), t('carePlan.colInstructions'), ''].map((h, i) => (
                <span key={i} className="label-caps">{h}</span>
              ))}
            </div>
            {draft.medicines.map((m) => (
              <div key={m.id} className="grid grid-cols-[1fr_90px_150px_1fr_28px] items-center gap-2">
                <Input value={m.name} placeholder="Metformin" onChange={(e) => updateMedicine(m.id, { name: e.target.value })} />
                <Input value={m.dose} placeholder="500 mg" onChange={(e) => updateMedicine(m.id, { dose: e.target.value })} />
                <Input
                  value={m.schedule.join(',')}
                  placeholder="08:00,20:00"
                  onChange={(e) => updateMedicine(m.id, { schedule: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
                />
                <Input value={m.instructions} placeholder="After food" onChange={(e) => updateMedicine(m.id, { instructions: e.target.value })} />
                <button
                  aria-label={`Remove ${m.name || 'medicine'}`}
                  onClick={() => update({ medicines: draft.medicines.filter((x) => x.id !== m.id) })}
                  className="grid h-8 place-items-center rounded text-faint hover:text-crit focus-ring cursor-pointer"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-faint">
          {t('carePlan.mergeNote', { language: languageLabel(patient.preferredLanguage) })}
        </p>
      </section>

      {/* Follow-up questions */}
      <section className="panel p-4">
        <SectionHeader
          title={t('carePlan.questions')}
          aside={
            <Button
              size="sm"
              onClick={() =>
                update({
                  questions: [
                    ...draft.questions,
                    { id: tmpId--, text: '', type: 'boolean', askAfterDays: 1, atTime: '10:00' },
                  ],
                })
              }
            >
              <Plus size={13} /> {t('carePlan.addQuestion')}
            </Button>
          }
        />
        {draft.questions.length === 0 ? (
          <p className="py-6 text-center text-xs text-mist">{t('carePlan.noQs')}</p>
        ) : (
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_110px_80px_90px_28px] gap-2 px-1">
              {[t('carePlan.colQuestion'), t('carePlan.colType'), t('carePlan.colDay'), t('carePlan.colTime'), ''].map((h, i) => (
                <span key={i} className="label-caps">{h}</span>
              ))}
            </div>
            {draft.questions.map((q) => (
              <div key={q.id} className="grid grid-cols-[1fr_110px_80px_90px_28px] items-center gap-2">
                <Input value={q.text} placeholder="Any swelling in your feet?" onChange={(e) => updateQuestion(q.id, { text: e.target.value })} />
                <Select value={q.type} onChange={(e) => updateQuestion(q.id, { type: e.target.value as FollowUpQuestion['type'] })}>
                  <option value="boolean">{t('carePlan.typeBoolean')}</option>
                  <option value="number">{t('carePlan.typeNumber')}</option>
                  <option value="enum">{t('carePlan.typeEnum')}</option>
                  <option value="short">{t('carePlan.typeShort')}</option>
                </Select>
                <Input type="number" min={0} value={q.askAfterDays} onChange={(e) => updateQuestion(q.id, { askAfterDays: Number(e.target.value) })} />
                <Input value={q.atTime} onChange={(e) => updateQuestion(q.id, { atTime: e.target.value })} />
                <button
                  aria-label="Remove question"
                  onClick={() => update({ questions: draft.questions.filter((x) => x.id !== q.id) })}
                  className="grid h-8 place-items-center rounded text-faint hover:text-crit focus-ring cursor-pointer"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Window + actions */}
      <section className="panel flex flex-wrap items-end gap-4 p-4">
        <Field label={t('carePlan.callWindow')} hint={t('carePlan.callWindowHint')}>
          <Input
            className="w-40"
            value={draft.callWindow}
            onChange={(e) => update({ callWindow: e.target.value })}
          />
        </Field>

        <div className="ml-auto flex items-center gap-3">
          {savedAt && !dirty && (
            <span className="text-[11px] text-mist">{t('carePlan.savedNote')}</span>
          )}
          <Button onClick={() => onSave()} disabled={save.isPending || !dirty}>
            {save.isPending ? t('carePlan.saving') : t('carePlan.save')}
          </Button>
          <Button
            variant={draft.carePlusEnabled ? 'danger' : 'primary'}
            onClick={() => onSave(!draft.carePlusEnabled)}
            disabled={save.isPending || (draft.medicines.length === 0 && !draft.carePlusEnabled)}
          >
            {draft.carePlusEnabled ? t('carePlan.pause') : t('carePlan.enable')}
          </Button>
        </div>
      </section>

      <div
        className={cx(
          'rounded-md border px-4 py-3 text-xs leading-relaxed',
          draft.carePlusEnabled ? 'border-accent/30 bg-accent/5 text-fog' : 'border-line text-mist',
        )}
      >
        {draft.carePlusEnabled
          ? t('carePlan.activeNote', {
              name: patient.name.split(' ')[0],
              language: languageLabel(patient.preferredLanguage),
            })
          : t('carePlan.inactiveNote')}
      </div>
    </div>
  )
}

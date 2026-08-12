import { useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Building2, FileText, FileUp, Loader2, UserRound } from 'lucide-react'
import type { HospitalDoc } from '@/api/types'
import { useDocuments, usePatients, useUploadDocument } from '@/api/hooks'
import { timeAgo } from '@/lib/format'
import { cx, Skeleton, Tag } from '@/components/ui/primitives'
import { ScopePicker } from '@/components/ScopePicker'

const TYPE_LABEL: Record<HospitalDoc['type'], string> = {
  guideline: 'Guideline',
  sop: 'SOP',
  discharge: 'Discharge',
  lab: 'Lab report',
  formulary: 'Formulary',
  prescription: 'Prescription',
}

export function Documents() {
  const { t } = useTranslation()
  const { data: docs } = useDocuments()
  const { data: patients } = usePatients()
  const upload = useUploadDocument()
  const [dragging, setDragging] = useState(false)
  // 'all' shows everything; 'general' = hospital-wide docs; number = one patient.
  const [scope, setScope] = useState<'all' | 'general' | number>('all')
  const inputRef = useRef<HTMLInputElement>(null)

  const uploadPatientId = typeof scope === 'number' ? scope : null
  const scopePatient = uploadPatientId != null ? patients?.find((p) => p.id === uploadPatientId) : undefined

  const visible = docs?.filter((d) =>
    scope === 'all' ? true : scope === 'general' ? d.patientId == null : d.patientId === scope,
  )

  function patientName(id: number | null): string | undefined {
    if (id == null) return undefined
    return patients?.find((p) => p.id === id)?.name ?? 'Patient'
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files?.[0]
    if (file) upload.mutate({ file, patientId: uploadPatientId })
  }

  return (
    <div className="space-y-5">
      {/* scope selector */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ScopePicker
          value={scope}
          onChange={(v) => setScope(v === 'all' || v === 'general' ? v : (v as number))}
          patients={patients}
          staticOptions={[
            { value: 'all', label: t('documents.scopeAll', { defaultValue: 'All documents' }), icon: 'files' },
            { value: 'general', label: t('documents.scopeGeneral', { defaultValue: 'General — hospital-wide' }), icon: 'building' },
          ]}
          triggerClassName="h-9"
        />
        <p className="text-[11px] text-faint">
          {scopePatient
            ? t('documents.uploadTargetPatient', {
                defaultValue: 'New uploads attach to {{name}} — visible in their Brain scope only.',
                name: scopePatient.name,
              })
            : t('documents.uploadTargetGeneral', {
                defaultValue: 'New uploads are general — available to every patient scope.',
              })}
        </p>
      </div>

      {/* dropzone */}
      <button
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cx(
          'flex w-full flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-9 transition-colors focus-ring cursor-pointer',
          dragging ? 'border-accent bg-accent/5' : 'border-line-strong hover:border-mist/50',
        )}
      >
        {upload.isPending ? (
          <Loader2 size={20} className="animate-spin text-accent" />
        ) : (
          <FileUp size={20} className="text-mist" />
        )}
        <span className="text-[13px] text-fog">
          {upload.isPending ? t('documents.uploading') : t('documents.dropTitle')}
        </span>
        <span className="text-[11px] text-faint">{t('documents.dropHint')}</span>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.md,.txt"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload.mutate({ file: f, patientId: uploadPatientId })
            e.target.value = ''
          }}
        />
      </button>

      {/* document grid */}
      {!visible ? (
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : visible.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line-strong px-4 py-8 text-center text-xs text-faint">
          {t('documents.scopeEmpty', { defaultValue: 'No documents in this scope yet — drop one above to ingest it.' })}
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {visible.map((d) => (
            <article key={d.id} className="panel flex flex-col p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <div className="grid size-8 shrink-0 place-items-center rounded-md bg-raised text-mist">
                    <FileText size={14} />
                  </div>
                  <div>
                    <h3 className="text-[13px] font-medium leading-snug text-bright">{d.title}</h3>
                    <p className="num mt-0.5 text-[11px] text-mist">
                      {TYPE_LABEL[d.type]} · {d.pages} pages · {d.sizeKb > 1024 ? `${(d.sizeKb / 1024).toFixed(1)} MB` : `${d.sizeKb} KB`} · {timeAgo(d.uploadedAt)}
                    </p>
                    <span
                      className={cx(
                        'mt-1.5 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium',
                        d.patientId != null
                          ? 'border-violet/30 bg-violet/10 text-violet'
                          : 'border-line-strong bg-raised text-mist',
                      )}
                    >
                      {d.patientId != null ? <UserRound size={9} /> : <Building2 size={9} />}
                      {patientName(d.patientId) ?? t('documents.general', { defaultValue: 'General' })}
                      {d.patientId != null && <span className="num opacity-70">#{d.patientId}</span>}
                    </span>
                  </div>
                </div>
                <StatusTag status={d.status} />
              </div>
              {d.excerpt && (
                <p className="mt-3 border-t border-line/60 pt-2.5 text-xs leading-relaxed text-mist line-clamp-3">
                  {d.excerpt}
                </p>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  )
}

function StatusTag({ status }: { status: HospitalDoc['status'] }) {
  const { t } = useTranslation()
  if (status === 'ready') return <Tag tone="good">{t('documents.indexed')}</Tag>
  if (status === 'failed') return <Tag tone="crit">{t('documents.failed')}</Tag>
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-warn">
      <Loader2 size={11} className="animate-spin" /> {t('documents.extracting')}
    </span>
  )
}

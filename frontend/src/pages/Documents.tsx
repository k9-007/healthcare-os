import { useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, FileUp, Loader2 } from 'lucide-react'
import type { HospitalDoc } from '@/api/types'
import { useDocuments, useUploadDocument } from '@/api/hooks'
import { timeAgo } from '@/lib/format'
import { cx, Skeleton, Tag } from '@/components/ui/primitives'

const TYPE_LABEL: Record<HospitalDoc['type'], string> = {
  guideline: 'Guideline',
  sop: 'SOP',
  discharge: 'Discharge',
  lab: 'Lab report',
  formulary: 'Formulary',
}

export function Documents() {
  const { t } = useTranslation()
  const { data: docs } = useDocuments()
  const upload = useUploadDocument()
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files?.[0]
    if (file) upload.mutate(file)
  }

  return (
    <div className="space-y-5">
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
            if (f) upload.mutate(f)
            e.target.value = ''
          }}
        />
      </button>

      {/* document grid */}
      {!docs ? (
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {docs.map((d) => (
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

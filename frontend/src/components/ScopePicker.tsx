import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Building2, Check, ChevronsUpDown, Files, Search, UserRound } from 'lucide-react'
import type { Patient } from '@/api/types'
import { cx } from '@/components/ui/primitives'

/** string = a static option (e.g. 'general', 'all'); number = a patient id. */
export type ScopeValue = string | number

export interface StaticScopeOption {
  value: string
  label: string
  icon?: 'building' | 'files'
}

const STATIC_ICONS = { building: Building2, files: Files }

/** Searchable patient scope dropdown. Patients are listed with their #id
 *  (names can collide) and can be found by name, #id or phone. */
export function ScopePicker({
  value,
  onChange,
  patients,
  staticOptions,
  direction = 'down',
  triggerClassName,
}: {
  value: ScopeValue
  onChange: (v: ScopeValue) => void
  patients?: Patient[]
  staticOptions: StaticScopeOption[]
  direction?: 'up' | 'down'
  triggerClassName?: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const selectedPatient = typeof value === 'number' ? patients?.find((p) => p.id === value) : undefined
  const selectedStatic = typeof value === 'string' ? staticOptions.find((o) => o.value === value) : undefined
  const isPatient = selectedPatient != null

  useEffect(() => {
    if (!open) return
    setQuery('')
    searchRef.current?.focus()
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const q = query.trim().toLowerCase()
  const idQuery = q.replace(/^#/, '')
  const matchedStatic = q
    ? staticOptions.filter((o) => o.label.toLowerCase().includes(q))
    : staticOptions
  const matchedPatients = useMemo(() => {
    if (!patients) return []
    if (!q) return patients
    return patients.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (/^\d+$/.test(idQuery) && String(p.id).startsWith(idQuery)) ||
        p.phone.replace(/[\s-]/g, '').includes(q.replace(/[\s-]/g, '')),
    )
  }, [patients, q, idQuery])

  function pick(v: ScopeValue) {
    onChange(v)
    setOpen(false)
  }

  function onSearchKey(e: React.KeyboardEvent) {
    if (e.key === 'Escape') setOpen(false)
    if (e.key === 'Enter') {
      const first = matchedStatic[0]?.value ?? matchedPatients[0]?.id
      if (first !== undefined) pick(first)
    }
  }

  const TriggerIcon = isPatient
    ? UserRound
    : STATIC_ICONS[selectedStatic?.icon ?? 'building']

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cx(
          'flex cursor-pointer items-center gap-1.5 rounded-lg border bg-panel px-2.5 text-[12px] focus-ring',
          isPatient ? 'border-violet/40 text-violet' : 'border-line-strong text-mist',
          triggerClassName,
        )}
      >
        <TriggerIcon size={13} className={isPatient ? 'text-violet' : 'text-faint'} />
        <span className="max-w-[110px] truncate">
          {selectedPatient?.name ?? selectedStatic?.label ?? staticOptions[0]?.label}
        </span>
        {isPatient && <span className="num text-[10.5px] opacity-70">#{selectedPatient.id}</span>}
        <ChevronsUpDown size={11} className="text-faint" />
      </button>

      {open && (
        <div
          className={cx(
            'absolute left-0 z-30 w-72 overflow-hidden rounded-lg border border-line-strong bg-panel shadow-2xl',
            direction === 'up' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          )}
        >
          <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
            <Search size={12} className="shrink-0 text-faint" />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onSearchKey}
              placeholder={t('scopePicker.search', { defaultValue: 'Search name, #id or phone…' })}
              className="w-full bg-transparent text-[12px] text-bright outline-none placeholder:text-faint"
            />
          </div>
          <ul className="max-h-72 overflow-y-auto p-1">
            {matchedStatic.map((o) => {
              const Icon = STATIC_ICONS[o.icon ?? 'building']
              return (
                <li key={o.value}>
                  <button
                    type="button"
                    onClick={() => pick(o.value)}
                    className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-raised"
                  >
                    <Icon size={13} className="shrink-0 text-mist" />
                    <span className="flex-1 truncate text-[12.5px] text-fog">{o.label}</span>
                    {value === o.value && <Check size={12} className="shrink-0 text-violet" />}
                  </button>
                </li>
              )
            })}
            {matchedStatic.length > 0 && matchedPatients.length > 0 && (
              <li className="mx-2 my-1 border-t border-line" aria-hidden />
            )}
            {matchedPatients.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => pick(p.id)}
                  className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-raised"
                >
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-violet/12 text-violet">
                    <UserRound size={11} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] leading-tight text-fog">{p.name}</span>
                    <span className="block truncate text-[10.5px] leading-tight text-faint">
                      {p.diagnosis} · {p.phone}
                    </span>
                  </span>
                  <span className="num shrink-0 rounded bg-violet/10 px-1.5 py-0.5 text-[10px] font-semibold text-violet">
                    #{p.id}
                  </span>
                  {value === p.id && <Check size={12} className="shrink-0 text-violet" />}
                </button>
              </li>
            ))}
            {matchedStatic.length === 0 && matchedPatients.length === 0 && (
              <li className="px-2 py-4 text-center text-[11.5px] text-faint">
                {t('scopePicker.empty', { defaultValue: 'No match — try a name, #id or phone.' })}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

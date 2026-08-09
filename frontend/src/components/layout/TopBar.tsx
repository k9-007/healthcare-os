import { useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Globe, Moon, Search, Sun } from 'lucide-react'
import { usePatients } from '@/api/hooks'
import { LANGUAGES, languageLabel } from '@/lib/languages'
import { useAppStore } from '@/store/useAppStore'
import { cx } from '@/components/ui/primitives'

const TITLE_KEYS: Record<string, string> = {
  '/dashboard': 'titles.dashboard',
  '/patients': 'titles.patients',
  '/brain': 'titles.brain',
  '/documents': 'titles.documents',
  '/settings': 'titles.settings',
}

export function TopBar() {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { uiLanguage, setUiLanguage, theme, toggleTheme } = useAppStore()
  const { data: patients } = usePatients()
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)

  const title = pathname.startsWith('/patients/')
    ? t('titles.patientRecord')
    : t(TITLE_KEYS[pathname] ?? 'brand.name')

  const hits = useMemo(() => {
    if (q.trim().length < 2 || !patients) return []
    const needle = q.toLowerCase()
    return patients
      .filter((p) => p.name.toLowerCase().includes(needle) || p.diagnosis.toLowerCase().includes(needle))
      .slice(0, 5)
  }, [q, patients])

  return (
    <header className="flex h-13 shrink-0 items-center gap-4 border-b border-line bg-panel px-6">
      <h1 className="text-[14px] font-semibold text-bright">{title}</h1>

      <div className="relative ml-auto w-64">
        <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-mist" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          placeholder={t('topbar.search')}
          className="h-8 w-full rounded-md border border-line bg-canvas pl-8 pr-3 text-xs text-bright placeholder:text-faint focus-ring"
        />
        {focused && hits.length > 0 && (
          <div className="panel absolute left-0 right-0 top-9.5 z-40 overflow-hidden py-1 shadow-xl shadow-black/40">
            {hits.map((p) => (
              <button
                key={p.id}
                className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-xs hover:bg-hover cursor-pointer"
                onMouseDown={() => {
                  navigate(`/patients/${p.id}`)
                  setQ('')
                }}
              >
                <span className="text-bright">{p.name}</span>
                <span className="truncate text-mist">{p.diagnosis}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        <Globe size={13} className="text-mist" />
        <select
          aria-label={t('topbar.uiLanguage')}
          value={uiLanguage}
          onChange={(e) => setUiLanguage(e.target.value)}
          className={cx(
            'h-8 cursor-pointer appearance-none rounded-md border border-line bg-canvas pl-2 pr-6 text-xs text-fog focus-ring',
          )}
        >
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>
              {languageLabel(l.code)} · {l.native}
            </option>
          ))}
        </select>
      </div>

      <button
        onClick={toggleTheme}
        aria-label={t('topbar.theme')}
        title={t('topbar.theme')}
        className="grid size-8 cursor-pointer place-items-center rounded-md border border-line bg-canvas text-mist transition-colors hover:text-fog focus-ring"
      >
        {theme === 'dark' ? <Sun size={13} /> : <Moon size={13} />}
      </button>
    </header>
  )
}

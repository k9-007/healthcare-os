import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Activity, BrainCircuit, FileText, LayoutGrid, Settings, Users,
} from 'lucide-react'
import { useEscalations, useUpcomingCalls } from '@/api/hooks'
import { cx } from '@/components/ui/primitives'

const NAV = [
  { to: '/dashboard', key: 'nav.dashboard', icon: LayoutGrid },
  { to: '/patients', key: 'nav.patients', icon: Users },
  { to: '/brain', key: 'nav.brain', icon: BrainCircuit },
  { to: '/documents', key: 'nav.documents', icon: FileText },
  { to: '/settings', key: 'nav.settings', icon: Settings },
] as const

export function Sidebar() {
  const { t } = useTranslation()
  const { data: escalations } = useEscalations()
  const { data: upcoming } = useUpcomingCalls()
  const open = escalations?.filter((e) => e.status === 'open').length ?? 0
  const queued = upcoming?.filter((c) => c.status === 'pending').length ?? 0

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-line bg-panel">
      <div className="flex items-start gap-2.5 px-5 pb-5 pt-5">
        <div className="grid size-7 shrink-0 place-items-center rounded-md bg-accent/12 text-accent">
          <Activity size={15} strokeWidth={2.2} />
        </div>
        <div className="leading-tight">
          <div className="font-serif text-[17px] tracking-tight text-bright">{t('brand.name')}</div>
          <div className="mt-0.5 text-[10px] leading-snug tracking-wide text-mist">{t('brand.tagline')}</div>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 px-3">
        {NAV.map(({ to, key, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cx(
                'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors focus-ring',
                isActive
                  ? 'bg-raised text-bright font-medium'
                  : 'text-mist hover:bg-hover hover:text-fog',
              )
            }
          >
            <Icon size={15} strokeWidth={1.8} />
            {t(key)}
            {to === '/dashboard' && open > 0 && (
              <span className="num ml-auto grid size-4.5 place-items-center rounded-full bg-crit/15 text-[10px] font-semibold text-crit pulse-crit">
                {open}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto space-y-2 px-5 pb-5">
        <div className="rounded-md border border-line px-3 py-2.5">
          <div className="label-caps mb-1">{t('sidebar.engine')}</div>
          <div className="flex items-center gap-1.5 text-xs text-fog">
            <span className="size-1.5 rounded-full bg-good" />
            {t('sidebar.engineStatus', { count: queued })}
          </div>
        </div>
        <p className="text-[10.5px] leading-relaxed text-faint">{t('sidebar.doctor')}</p>
      </div>
    </aside>
  )
}

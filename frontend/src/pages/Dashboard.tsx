import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { PhoneOutgoing, TrendingDown, TrendingUp } from 'lucide-react'
import { useAckEscalation, useAnalytics, useEscalations, useUpcomingCalls } from '@/api/hooks'
import { languageLabel } from '@/lib/languages'
import { clockTime, timeAgo, timeUntil } from '@/lib/format'
import { useAppStore } from '@/store/useAppStore'
import { Button, cx, EmptyState, SectionHeader, Skeleton, Tag } from '@/components/ui/primitives'

/* Recharts sets SVG attributes, which can't resolve CSS variables — pick per theme. */
const CHART_COLORS = {
  dark: { grid: '#1c2740', tick: '#6d7fa3', tooltipBg: '#121b30', tooltipBorder: '#283757', label: '#aebbd6', accent: '#45d0c0' },
  light: { grid: '#dfe5f0', tick: '#67769a', tooltipBg: '#ffffff', tooltipBorder: '#c4cfe2', label: '#3c4b6b', accent: '#0f9a8c' },
}

export function Dashboard() {
  const { t } = useTranslation()
  const { data: a } = useAnalytics()
  const theme = useAppStore((s) => s.theme)
  const c = CHART_COLORS[theme]

  return (
    <div className="space-y-6">
      {/* KPI strip */}
      <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line md:grid-cols-3 xl:grid-cols-6">
        {a ? (
          <>
            <Kpi label={t('dashboard.kpiAdherence')} value={`${a.adherence}%`} delta={a.adherenceDelta} />
            <Kpi label={t('dashboard.kpiMissed')} value={String(a.missedDoses7d)} tone={a.missedDoses7d > 15 ? 'crit' : 'neutral'} />
            <Kpi label={t('dashboard.kpiAtRisk')} value={String(a.patientsAtRisk)} tone={a.patientsAtRisk > 0 ? 'warn' : 'good'} />
            <Kpi label={t('dashboard.kpiEscalations')} value={String(a.openEscalations)} tone={a.openEscalations > 0 ? 'crit' : 'good'} />
            <Kpi label={t('dashboard.kpiFollowup')} value={`${a.followupCompletion}%`} />
            <Kpi label={t('dashboard.kpiCallSuccess')} value={`${a.callSuccessRate}%`} />
          </>
        ) : (
          Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-none" />)
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Adherence trend */}
        <section className="panel p-4 lg:col-span-3">
          <SectionHeader
            title={t('dashboard.trendTitle')}
            aside={a && <span className="num text-xs text-mist">{t('dashboard.activePatients', { count: a.activePatients })}</span>}
          />
          {a ? (
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={a.trend} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
                <defs>
                  <linearGradient id="adh" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={c.accent} stopOpacity={0.22} />
                    <stop offset="100%" stopColor={c.accent} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="day" tick={{ fill: c.tick, fontSize: 10.5 }} tickLine={false} axisLine={false} interval={2} />
                <YAxis domain={[60, 100]} tick={{ fill: c.tick, fontSize: 10.5 }} tickLine={false} axisLine={false} width={46} />
                <Tooltip
                  contentStyle={{ background: c.tooltipBg, border: `1px solid ${c.tooltipBorder}`, borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: c.label }}
                  itemStyle={{ color: c.accent }}
                  formatter={(v) => [`${v}%`, t('dashboard.kpiAdherence').toLowerCase()]}
                />
                <Area type="monotone" dataKey="adherence" stroke={c.accent} strokeWidth={1.6} fill="url(#adh)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="h-52" />
          )}
        </section>

        {/* Escalation feed */}
        <section className="panel p-4 lg:col-span-2">
          <SectionHeader title={t('dashboard.escalations')} />
          <EscalationFeed />
        </section>
      </div>

      {/* Upcoming calls queue */}
      <section className="panel p-4">
        <SectionHeader
          title={t('dashboard.upcomingTitle')}
          aside={<span className="flex items-center gap-1.5 text-xs text-mist"><span className="size-1.5 rounded-full bg-good" />{t('dashboard.tick')}</span>}
        />
        <UpcomingCalls />
      </section>
    </div>
  )
}

function Kpi({ label, value, delta, tone = 'neutral' }: {
  label: string
  value: string
  delta?: number
  tone?: 'neutral' | 'good' | 'warn' | 'crit'
}) {
  const valueCls = { neutral: 'text-bright', good: 'text-good', warn: 'text-warn', crit: 'text-crit' }[tone]
  return (
    <div className="bg-panel px-4 py-3.5">
      <div className="label-caps">{label}</div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className={cx('num text-[22px] font-semibold tracking-tight', valueCls)}>{value}</span>
        {delta !== undefined && (
          <span className={cx('flex items-center gap-0.5 text-[11px] num', delta >= 0 ? 'text-good' : 'text-crit')}>
            {delta >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
            {Math.abs(delta)}%
          </span>
        )}
      </div>
    </div>
  )
}

function EscalationFeed() {
  const { t } = useTranslation()
  const { data: escalations } = useEscalations()
  const ack = useAckEscalation()

  if (!escalations) return <Skeleton className="h-52" />
  const active = escalations.filter((e) => e.status !== 'closed')
  if (active.length === 0) {
    return <EmptyState title={t('dashboard.emptyEscalations')} hint={t('dashboard.emptyEscalationsHint')} />
  }

  return (
    <ul className="space-y-2.5">
      {active.map((e) => (
        <li
          key={e.id}
          className={cx(
            'rounded-md border p-3',
            e.status === 'open' && e.urgency === 'high' ? 'border-crit/40 bg-crit/6 pulse-crit' : 'border-line',
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <Link to={`/patients/${e.patientId}`} className="text-[13px] font-medium text-bright hover:text-accent">
              {e.patientName}
            </Link>
            <span className="flex items-center gap-2">
              <Tag tone={e.urgency === 'high' ? 'crit' : e.urgency === 'medium' ? 'warn' : 'neutral'}>{e.urgency}</Tag>
              <span className="num text-[11px] text-mist">{timeAgo(e.createdAt)}</span>
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-fog">{e.reason}</p>
          {e.status === 'open' ? (
            <Button size="sm" className="mt-2" onClick={() => ack.mutate(e.id)} disabled={ack.isPending}>
              {t('dashboard.acknowledge')}
            </Button>
          ) : (
            <span className="mt-2 inline-block text-[11px] text-mist">{t('dashboard.acknowledged')}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

function UpcomingCalls() {
  const { t } = useTranslation()
  const { data: calls } = useUpcomingCalls()
  if (!calls) return <Skeleton className="h-40" />

  const cols = [
    t('dashboard.colDue'),
    t('dashboard.colPatient'),
    t('dashboard.colKind'),
    t('dashboard.colCovers'),
    t('dashboard.colLanguage'),
    '',
  ]

  return (
    <table className="w-full text-left text-[13px]">
      <thead>
        <tr className="border-b border-line">
          {cols.map((h, i) => (
            <th key={i} className="label-caps pb-2 pr-4 font-semibold">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {calls.map((c) => (
          <tr key={c.id} className="border-b border-line/60 last:border-0">
            <td className="num py-2.5 pr-4 whitespace-nowrap">
              <span className="text-bright">{clockTime(c.dueAt)}</span>
              <span className="ml-2 text-[11px] text-mist">{timeUntil(c.dueAt)}</span>
            </td>
            <td className="py-2.5 pr-4">
              <Link to={`/patients/${c.patientId}`} className="text-fog hover:text-accent">{c.patientName}</Link>
            </td>
            <td className="py-2.5 pr-4">
              <Tag tone={c.kind === 'callback' ? 'accent' : 'neutral'}>{t(`kind.${c.kind}`)}</Tag>
            </td>
            <td className="py-2.5 pr-4 text-fog">{c.targets.join(' · ')}</td>
            <td className="py-2.5 pr-4 text-mist">{languageLabel(c.language)}</td>
            <td className="py-2.5 text-right">
              <PhoneOutgoing size={13} className="inline text-faint" />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

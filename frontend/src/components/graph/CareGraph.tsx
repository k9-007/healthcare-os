import { motion } from 'framer-motion'
import {
  AlertTriangle, Bell, HeartPulse, MessageCircle, PhoneCall, Pill, Stethoscope, Sunrise,
} from 'lucide-react'
import type { CareEvent } from '@/api/types'
import { clockTime, shortDate } from '@/lib/format'
import { cx, EmptyState, Skeleton } from '@/components/ui/primitives'

const ICONS: Record<CareEvent['type'], typeof Pill> = {
  discharge: Sunrise,
  med_started: Pill,
  call: PhoneCall,
  missed_dose: Bell,
  symptom: HeartPulse,
  alert: AlertTriangle,
  advice: MessageCircle,
  recovered: Stethoscope,
}

export function CareGraph({ events }: { events?: CareEvent[] }) {
  if (!events) return <Skeleton className="h-72" />
  if (events.length === 0) {
    return (
      <div className="panel">
        <EmptyState title="No journey yet" hint="Events appear here from discharge through recovery." />
      </div>
    )
  }

  // chronological, oldest first — a story that reads downwards
  const ordered = [...events].sort((a, b) => a.ts.localeCompare(b.ts))

  return (
    <div className="panel px-5 py-6">
      <ol className="relative ml-3 border-l border-line-strong">
        {ordered.map((e, i) => {
          const Icon = ICONS[e.type]
          const tone =
            e.severity === 'critical' ? 'text-crit border-crit/40 bg-crit/10'
            : e.severity === 'warn' ? 'text-warn border-warn/40 bg-warn/10'
            : e.type === 'recovered' ? 'text-good border-good/40 bg-good/10'
            : 'text-accent border-line-strong bg-raised'
          return (
            <motion.li
              key={e.id}
              className="relative mb-6 pl-8 last:mb-0"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05, duration: 0.25 }}
            >
              <span
                className={cx(
                  'absolute -left-[13px] top-0 grid size-6.5 place-items-center rounded-full border',
                  tone,
                  e.severity === 'critical' && 'pulse-crit',
                )}
              >
                <Icon size={12} strokeWidth={2} />
              </span>
              <div className="flex flex-wrap items-baseline gap-x-3">
                <h3 className="text-[13px] font-medium text-bright">{e.title}</h3>
                <span className="num text-[11px] text-faint">
                  {shortDate(e.ts)} · {clockTime(e.ts)}
                </span>
              </div>
              <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-fog">{e.detail}</p>
            </motion.li>
          )
        })}
      </ol>
    </div>
  )
}

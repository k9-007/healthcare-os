import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'
import type { Risk } from '@/api/types'

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ')
}

/* ---------- buttons ---------- */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}

export function Button({ variant = 'ghost', size = 'md', className, ...rest }: ButtonProps) {
  return (
    <button
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md font-medium transition-colors focus-ring disabled:opacity-45 disabled:pointer-events-none cursor-pointer',
        size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8.5 px-3.5 text-[13px]',
        variant === 'primary' && 'bg-accent text-canvas hover:bg-accent/85',
        variant === 'ghost' && 'border border-line-strong text-fog hover:bg-hover hover:text-bright',
        variant === 'danger' && 'border border-crit/40 text-crit hover:bg-crit/10',
        className,
      )}
      {...rest}
    />
  )
}

/* ---------- form fields ---------- */

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label-caps mb-1.5 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-mist">{hint}</span>}
    </label>
  )
}

const inputCls =
  'w-full h-8.5 rounded-md border border-line-strong bg-canvas px-2.5 text-[13px] text-bright placeholder:text-faint focus-ring'

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputCls, 'appearance-none pr-7', props.className)} />
}

/* ---------- badges & indicators ---------- */

export function RiskBadge({ risk }: { risk: Risk }) {
  const { t } = useTranslation()
  const map = {
    low: 'text-good border-good/30 bg-good/8',
    medium: 'text-warn border-warn/30 bg-warn/8',
    high: 'text-crit border-crit/30 bg-crit/8',
  }
  const label = { low: t('risk.stable'), medium: t('risk.watch'), high: t('risk.atRisk') }[risk]
  return (
    <span className={cx('inline-flex items-center rounded px-1.5 py-px text-[11px] font-medium border', map[risk])}>
      {label}
    </span>
  )
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warn' | 'crit' | 'accent' }) {
  const map = {
    neutral: 'text-mist border-line-strong',
    good: 'text-good border-good/30',
    warn: 'text-warn border-warn/30',
    crit: 'text-crit border-crit/30',
    accent: 'text-accent border-accent/30',
  }
  return (
    <span className={cx('inline-flex items-center rounded border px-1.5 py-px text-[11px]', map[tone])}>
      {children}
    </span>
  )
}

export function AdherenceBar({ value }: { value: number }) {
  const tone = value >= 85 ? 'bg-good' : value >= 70 ? 'bg-warn' : 'bg-crit'
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1 w-14 overflow-hidden rounded-full bg-line">
        <span className={cx('block h-full rounded-full', tone)} style={{ width: `${value}%` }} />
      </span>
      <span className="num text-xs text-fog">{value}%</span>
    </span>
  )
}

/* ---------- loading / empty ---------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-md bg-raised', className)} />
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
      <p className="text-sm text-fog">{title}</p>
      {hint && <p className="text-xs text-mist">{hint}</p>}
    </div>
  )
}

/* ---------- section header ---------- */

export function SectionHeader({ title, aside }: { title: string; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h2 className="label-caps">{title}</h2>
      {aside}
    </div>
  )
}

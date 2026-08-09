export function timeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime()
  const min = Math.round(diff / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m ago`
  const h = Math.round(min / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  return d === 1 ? 'yesterday' : `${d}d ago`
}

export function timeUntil(isoDate: string): string {
  const diff = new Date(isoDate).getTime() - Date.now()
  if (diff <= 0) return 'due now'
  const min = Math.round(diff / 60_000)
  if (min < 60) return `in ${min}m`
  const h = Math.floor(min / 60)
  if (h < 24) return `in ${h}h ${min % 60 ? `${min % 60}m` : ''}`.trim()
  return `in ${Math.round(h / 24)}d`
}

export function shortDate(isoDate: string): string {
  return new Date(isoDate).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export function clockTime(isoDate: string): string {
  return new Date(isoDate).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true })
}

export function callDuration(sec: number): string {
  if (!sec) return '—'
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

export function initials(name: string): string {
  return name.split(' ').map((n) => n[0]).slice(0, 2).join('').toUpperCase()
}

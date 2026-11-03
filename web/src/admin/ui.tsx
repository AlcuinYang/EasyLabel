import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ApiError, request } from '../shared/api'

export const STATUS: Record<string, string> = { draft: '草稿', frozen: '已冻结', live: '进行中', closed: '已关闭' }
export const TEMPLATE: Record<string, string> = { grid: 'A 多选网格', pair: 'B 成对比较', region: 'C 区域点选' }
export const TIER: Record<string, string> = { lab: '实验室', remote: '远程' }

export function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    if (!path) return
    request<T>(path).then((d) => { setData(d); setError(null) }, (e: ApiError) => {
      if (e.status === 401) window.dispatchEvent(new Event('el-signed-out'))
      setError(e.message)
    })
  }, [path])
  useEffect(reload, [reload])
  return { data, error, reload }
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}><i />{STATUS[status] ?? status}</span>
}

export function Meter({ value, max }: { value: number; max: number }) {
  const pct = max ? Math.min(100, (100 * value) / max) : 0
  return <div className="meter" role="meter" aria-valuenow={value} aria-valuemax={max}><div style={{ width: `${pct}%` }} /></div>
}

export function Panel({ title, aside, flush, children }: { title?: string; aside?: ReactNode; flush?: boolean; children: ReactNode }) {
  return (
    <section className={`panel ${flush ? 'flush' : ''}`}>
      {(title || aside) && <div className="panel-head">{title && <h2>{title}</h2>}{aside}</div>}
      {children}
    </section>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>
}

export function Copy({ text, label = '复制' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => setDone(false), 1500)
    return () => clearTimeout(t)
  }, [done])
  return (
    <button className="btn sm" onClick={() => void navigator.clipboard?.writeText(text).then(() => setDone(true), () => undefined)}>
      {done ? '已复制' : label}
    </button>
  )
}

export function relTime(iso: string | null): string {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 天前`
  return iso.slice(0, 10)
}

export const pct = (x: number | null | undefined, digits = 0) => (x == null ? '—' : `${(100 * x).toFixed(digits)}%`)

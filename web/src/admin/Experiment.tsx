import { useState } from 'react'
import { post } from '../shared/api'
import { ConfigPanel, InvitesPanel, PreviewPanel, ProgressPanel, RatersPanel } from './panels'
import type { Detail } from './types'
import { Copy, Meter, StatusBadge, TEMPLATE, relTime, useLoad } from './ui'

const LIFECYCLE = [['draft', '草稿'], ['frozen', '已冻结'], ['live', '进行中'], ['closed', '已关闭']]
const NEXT: Record<string, { action: string; label: string; confirm: string; primary: boolean }> = {
  draft: { action: 'freeze', label: '冻结配置', confirm: '冻结后配置、题目和图片都不可再改。确定冻结？', primary: true },
  frozen: { action: 'publish', label: '发布', confirm: '发布后邀请链接即可使用。确定发布？', primary: true },
  live: { action: 'close', label: '关闭收集', confirm: '关闭后评分者不能再开始新题块。确定关闭？', primary: false },
}
const TABS = [['progress', '进度'], ['raters', '评分者'], ['invites', '邀请'], ['preview', '预览'], ['config', '配置']]

export function ExperimentPage({ id }: { id: string }) {
  const base = `/api/admin/experiments/${encodeURIComponent(id)}`
  const { data, error, reload } = useLoad<Detail>(base)
  const [tab, setTab] = useState('progress')
  const [message, setMessage] = useState<string | null>(null)
  if (error) return <div className="error-box">{error}</div>
  if (!data) return null
  const c = data.config
  const s = data.summary
  const next = NEXT[data.status]
  const at = LIFECYCLE.findIndex(([k]) => k === data.status)
  const act = async () => {
    if (!next || !window.confirm(next.confirm)) return
    try {
      await post(`${base}/${next.action}`)
      setMessage(null)
      reload()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
  const main = data.items.main ?? 0
  const peak = Math.max(1, ...s.daily.map((d) => d.n))
  const days = s.daily.slice(-21)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{c.title} <StatusBadge status={data.status} /></h1>
          <div className="meta">
            <span><b>{TEMPLATE[data.template] ?? data.template}</b></span>
            <span>正式 <b>{main}</b> 题 · 练习 <b>{data.items.practice ?? 0}</b> · 金标 <b>{data.items.gold ?? 0}</b></span>
            <span>每题目标 <b>{c.target_per_item}</b> 人次 · 每块 <b>{c.block_size}</b> 题</span>
            <span>创建于 {data.created_at.slice(0, 10)}</span>
          </div>
        </div>
        <div className="row">
          <a className="btn" href={`${base}/export.zip`}>导出数据</a>
          {next && <button className={`btn ${next.primary ? 'primary' : 'danger'}`} onClick={act}>{next.label}</button>}
        </div>
      </div>

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <ol className="lifecycle" aria-label="实验状态">
          {LIFECYCLE.map(([k, label], i) => (
            <li key={k} className={i < at ? 'done' : i === at ? 'now' : ''}><i />{label}</li>
          ))}
        </ol>
        {data.config_hash && (
          <span className="row small muted">
            配置哈希 <code>{data.config_hash.slice(0, 12)}</code><Copy text={data.config_hash} />
            · 代码 <code>{data.code_version ?? '未知'}</code>
          </span>
        )}
      </div>
      {message && <div className="error-box">{message}</div>}

      <div className="kpis">
        <div className="kpi">
          <span>评分者</span>
          <strong>{s.raters_started}<small>/ {s.raters} 已报名</small></strong>
          <em>已开始正式作答的人数</em>
        </div>
        <div className="kpi">
          <span>正式作答</span>
          <strong>{s.responses}<small>条 · 今天 {s.daily.find((d) => d.date === today())?.n ?? 0}</small></strong>
          {days.length > 1 ? (
            <div className="spark" aria-label="近 21 天每日作答数">
              {days.map((d) => <div key={d.date} style={{ height: `${(100 * d.n) / peak}%` }} title={`${d.date}：${d.n} 条`} />)}
            </div>
          ) : <em>收集满两天后显示每日趋势</em>}
        </div>
        <TargetKpi base={base} />
        <div className="kpi">
          <span>最近活动</span>
          <strong style={{ fontSize: 20 }}>{relTime(s.last_activity)}</strong>
          <em>{s.daily.length ? `已收集 ${s.daily.length} 天` : '还没有作答'}</em>
        </div>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {label}{k === 'invites' && <span className="count">{data.invites.length}</span>}
          </button>
        ))}
      </div>
      {tab === 'progress' && <ProgressPanel base={base} />}
      {tab === 'raters' && <RatersPanel base={base} exclusion={c.exclusion} />}
      {tab === 'invites' && <InvitesPanel base={base} detail={data} reload={reload} />}
      {tab === 'preview' && <PreviewPanel base={base} />}
      {tab === 'config' && <ConfigPanel detail={data} />}
    </>
  )
}

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function TargetKpi({ base }: { base: string }) {
  const { data } = useLoad<{ total_items: number; items_at_target: number; target: number }>(`${base}/progress`)
  return (
    <div className="kpi">
      <span>达到目标人次的题目</span>
      <strong>{data ? data.items_at_target : '—'}<small>/ {data?.total_items ?? '—'}</small></strong>
      {data && <Meter value={data.items_at_target} max={data.total_items} />}
    </div>
  )
}

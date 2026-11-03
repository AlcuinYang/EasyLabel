import { useCallback, useEffect, useState } from 'react'
import { ApiError, post, request } from '../shared/api'
import type { Detail, ExperimentRow, Progress, RaterCard } from './types'

const STATUS: Record<string, string> = { draft: '草稿', frozen: '已冻结', live: '进行中', closed: '已关闭' }
const TEMPLATE: Record<string, string> = { grid: 'A 多选网格', pair: 'B 成对比较', region: 'C 区域点选' }

function useHash() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

export function App() {
  const hash = useHash()
  const match = hash.match(/^#\/exp\/(.+)$/)
  return (
    <>
      <header><h1><a href="#/">EasyLabel 管理</a></h1>{match && <span className="muted">/ {decodeURIComponent(match[1])}</span>}</header>
      <main>{match ? <ExperimentPage id={decodeURIComponent(match[1])} /> : <ExperimentList />}</main>
    </>
  )
}

function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    if (!path) return
    request<T>(path).then((d) => { setData(d); setError(null) }, (e: ApiError) =>
      setError(e.status === 401 ? '未登录：请打开服务启动时打印的 /admin/login?token=… 链接' : e.message))
  }, [path])
  useEffect(reload, [reload])
  return { data, error, reload }
}

function ExperimentList() {
  const { data, error } = useLoad<ExperimentRow[]>('/api/admin/experiments')
  if (error) return <div className="panel error">{error}</div>
  if (!data) return null
  return (
    <div className="panel">
      <h2>实验</h2>
      {data.length === 0 ? <p className="muted">还没有实验。用 easylabel create-experiment 导入配置。</p> : (
        <table>
          <thead><tr><th>实验</th><th>模板</th><th>状态</th><th className="num">评分者</th><th className="num">已答</th><th>创建</th></tr></thead>
          <tbody>{data.map((e) => (
            <tr key={e.id}>
              <td><a href={`#/exp/${encodeURIComponent(e.id)}`}>{e.title}</a><div className="muted">{e.id}</div></td>
              <td>{TEMPLATE[e.template] ?? e.template}</td>
              <td><span className={`badge ${e.status}`}>{STATUS[e.status] ?? e.status}</span></td>
              <td className="num">{e.raters}</td><td className="num">{e.answered}</td>
              <td className="muted">{e.created_at.slice(0, 10)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  )
}

function ExperimentPage({ id }: { id: string }) {
  const base = `/api/admin/experiments/${encodeURIComponent(id)}`
  const { data, error, reload } = useLoad<Detail>(base)
  const [tab, setTab] = useState('progress')
  const [message, setMessage] = useState<string | null>(null)
  if (error) return <div className="panel error">{error}</div>
  if (!data) return null
  const act = async (action: string, confirmText: string) => {
    if (!window.confirm(confirmText)) return
    try {
      await post(`${base}/${action}`)
      setMessage(null)
      reload()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
  const c = data.config
  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <h2 style={{ marginBottom: 4 }}>{c.title} <span className={`badge ${data.status}`}>{STATUS[data.status]}</span></h2>
            <div className="muted">{TEMPLATE[data.template]} · 正式 {data.items.main ?? 0} 题 · 练习 {data.items.practice ?? 0} · 金标 {data.items.gold ?? 0}
              · 每题目标 {c.target_per_item} 人次 · 每块 {c.block_size} 题</div>
            {data.config_hash && <div className="muted">配置哈希 {data.config_hash.slice(0, 16)}… · 代码 {data.code_version ?? '未知'}</div>}
          </div>
          <div className="row">
            {data.status === 'draft' && <button className="primary" onClick={() => act('freeze', '冻结后配置不可再改。确定？')}>冻结</button>}
            {data.status === 'frozen' && <button className="primary" onClick={() => act('publish', '发布后邀请链接即可使用。确定？')}>发布</button>}
            {data.status === 'live' && <button onClick={() => act('close', '关闭后评分者不能再开始新题块。确定？')}>关闭</button>}
            <a href={`${base}/export.zip`}><button>导出</button></a>
          </div>
        </div>
        {message && <p className="error">{message}</p>}
      </div>
      <div className="tabs">
        {[['progress', '进度'], ['raters', '评分者质控'], ['invites', '邀请'], ['preview', '预览'], ['config', '配置']].map(([k, label]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === 'progress' && <ProgressPanel base={base} />}
      {tab === 'raters' && <RatersPanel base={base} />}
      {tab === 'invites' && <InvitesPanel base={base} detail={data} reload={reload} />}
      {tab === 'preview' && <PreviewPanel base={base} />}
      {tab === 'config' && <div className="panel"><pre>{JSON.stringify(c, null, 2)}</pre></div>}
    </>
  )
}

function cellColor(count: number, target: number) {
  if (count === 0) return 'var(--none)'
  if (count >= target) return 'var(--seq-done)'
  const r = count / target
  return r < 0.25 ? 'var(--seq-1)' : r < 0.5 ? 'var(--seq-2)' : r < 0.75 ? 'var(--seq-3)' : 'var(--seq-4)'
}

function ProgressPanel({ base }: { base: string }) {
  const { data, error } = useLoad<Progress>(`${base}/progress`)
  if (error) return <div className="panel error">{error}</div>
  if (!data) return null
  const pct = data.total_items ? Math.round((100 * data.items_at_target) / data.total_items) : 0
  return (
    <div className="panel">
      <div className="stats" style={{ marginBottom: 16 }}>
        <div className="stat"><strong>{data.items_at_target} / {data.total_items}</strong><span className="muted">题达到 {data.target} 人次（{pct}%）</span></div>
        <div className="stat"><strong>{data.scenes.length}</strong><span className="muted">场景</span></div>
      </div>
      <div className="legend" style={{ marginBottom: 10 }}>
        <span>每格一道题，颜色为已完成人次：</span>
        <span><i style={{ background: 'var(--none)' }} />0</span>
        <span><i style={{ background: 'var(--seq-1)' }} />&lt;25%</span>
        <span><i style={{ background: 'var(--seq-2)' }} />&lt;50%</span>
        <span><i style={{ background: 'var(--seq-3)' }} />&lt;75%</span>
        <span><i style={{ background: 'var(--seq-4)' }} />&lt;100%</span>
        <span><i style={{ background: 'var(--seq-done)' }} />达到目标</span>
      </div>
      <div className="heat">
        {data.scenes.map((s) => (
          <div className="line" key={s.scene_id}>
            <span>{s.scene_id}{s.core ? <span className="muted"> · 核心 {s.core}</span> : null}</span>
            <div className="cells">
              {s.counts.map((n, i) => <div key={i} className="cell" style={{ background: cellColor(n, data.target) }}
                title={`${s.scene_id} 第 ${i + 1} 题：${n} 人次`} />)}
            </div>
            <span className="muted">{s.at_target}/{s.items} 达标 · 最少 {s.min}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const fmt = (r: { rate: number | null; n: number }) => (r.n ? `${(100 * (r.rate ?? 0)).toFixed(0)}%（${r.n}）` : '—')

function RatersPanel({ base }: { base: string }) {
  const { data, error } = useLoad<RaterCard[]>(`${base}/raters`)
  if (error) return <div className="panel error">{error}</div>
  if (!data) return null
  if (!data.length) return <div className="panel muted">还没有正式作答。</div>
  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0 }}>排除规则来自冻结配置；这里只标记，不删除数据。</p>
      <div className="cards">
        {data.map((r) => (
          <div key={r.rater} className={`card ${r.excluded ? 'excluded' : ''}`}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{r.rater}</strong><span className="badge">{r.tier === 'lab' ? '实验室' : '远程'} · {r.arm}</span>
            </div>
            <div className="kv"><span>正式作答</span><span>{r.n_main}</span></div>
            <div className="kv"><span>自一致率</span><span>{fmt(r.self_consistency)}</span></div>
            <div className="kv"><span>互换一致率</span><span>{fmt(r.swap_consistency)}</span></div>
            <div className="kv"><span>金标题正确率</span><span>{fmt(r.gold_accuracy)}</span></div>
            <div className="kv"><span>反应时中位数</span><span>{r.rt_median_ms ? `${(r.rt_median_ms / 1000).toFixed(1)} 秒` : '—'}</span></div>
            <div className="kv"><span>过快作答占比</span><span>{r.rt_fast_share == null ? '—' : `${(100 * r.rt_fast_share).toFixed(0)}%`}</span></div>
            <div className="kv"><span>选左占比</span><span>{r.left_share == null ? '—' : `${(100 * r.left_share).toFixed(0)}%`}</span></div>
            {r.display && <div className="kv"><span>显示</span><span>
              {r.display.screen ? `${r.display.screen.width}×${r.display.screen.height}` : ''} @{r.display.dpr}× · 黑 {r.display.black} 白 {r.display.white}
              {r.display.passed ? '' : ' · 未通过'}</span></div>}
            {r.display?.reasons.map((x) => <div key={x} className="muted">{x}</div>)}
            {r.exclusion_reasons.map((x) => <div key={x} className="flag">排除：{x}</div>)}
          </div>
        ))}
      </div>
    </div>
  )
}

function InvitesPanel({ base, detail, reload }: { base: string; detail: Detail; reload: () => void }) {
  const [form, setForm] = useState({ tier: detail.config.tiers?.[0] ?? 'remote', count: 1, max_uses: 1, arm: '', note: '' })
  const [error, setError] = useState<string | null>(null)
  const arms: { id: string }[] = detail.config.arms ?? []
  const create = async () => {
    try {
      await post(`${base}/invites`, form)
      setError(null)
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 14 }}>
        <select value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })}>
          {(detail.config.tiers ?? []).map((t: string) => <option key={t} value={t}>{t === 'lab' ? '实验室档' : '远程档'}</option>)}
        </select>
        {arms.length > 1 && (
          <select value={form.arm} onChange={(e) => setForm({ ...form, arm: e.target.value })}>
            <option value="">自动均衡分组</option>
            {arms.map((a) => <option key={a.id} value={a.id}>{a.id}</option>)}
          </select>
        )}
        <label>数量 <input type="number" min={1} max={500} value={form.count} style={{ width: 70 }}
          onChange={(e) => setForm({ ...form, count: Number(e.target.value) })} /></label>
        <label>每个链接可用 <input type="number" min={1} value={form.max_uses} style={{ width: 70 }}
          onChange={(e) => setForm({ ...form, max_uses: Number(e.target.value) })} /> 人</label>
        <input placeholder="备注" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        <button className="primary" onClick={create}>生成</button>
      </div>
      {error && <p className="error">{error}</p>}
      <p className="muted">众包平台可在链接后加 ?src=平台&amp;pid=参与者编号；完成一块后显示完成码。</p>
      <table>
        <thead><tr><th>链接</th><th>档位</th><th>分组</th><th className="num">已用</th><th>备注</th></tr></thead>
        <tbody>{detail.invites.map((i) => (
          <tr key={i.url}><td><code>{window.location.origin}{i.url}</code></td><td>{i.tier}</td><td>{i.arm ?? '自动'}</td>
            <td className="num">{i.uses}/{i.max_uses}</td><td className="muted">{i.note}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

interface Preview {
  template: string; arms: { id: string; question: string }[]; choices: string[] | null
  items: { item_key: string; kind: string; scene_id: string; images: { slot: number; main: { css_width: number; css_height: number; src: Record<string, string> } }[] }[]
}

function PreviewPanel({ base }: { base: string }) {
  const { data, error } = useLoad<Preview>(`${base}/preview?n=6`)
  if (error) return <div className="panel error">{error}</div>
  if (!data) return null
  const scale = window.devicePixelRatio >= 1.75 ? '2' : window.devicePixelRatio >= 1.25 ? '1.5' : '1'
  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0 }}>按评分端尺寸显示前几道题（不记录作答）。问题：{data.arms.map((a) => `【${a.id}】${a.question}`).join('　')}</p>
      <div className="preview">
        {data.items.map((it) => (
          <div className="item" key={it.kind + it.item_key}>
            <span>{it.kind} · {it.scene_id} · {it.item_key}</span>
            <div className="imgs">
              {it.images.map((im) => <img key={im.slot} src={im.main.src[scale]} width={im.main.css_width} height={im.main.css_height} alt="" />)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

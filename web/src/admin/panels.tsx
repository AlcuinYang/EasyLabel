import { Fragment, useMemo, useState } from 'react'
import { post } from '../shared/api'
import type { Detail, Progress, Rate, RaterCard } from './types'
import { Copy, Empty, Meter, Panel, TEMPLATE, TIER, pct, useLoad } from './ui'

// ---------------------------------------------------------------- progress

const RAMP: [string, string][] = [
  ['var(--none)', '0'], ['var(--seq-1)', '< 25%'], ['var(--seq-2)', '< 50%'], ['var(--seq-3)', '< 75%'],
  ['var(--seq-4)', '< 100%'], ['var(--seq-done)', '达到目标'],
]

function cellColor(count: number, target: number) {
  if (count === 0) return RAMP[0][0]
  if (count >= target) return RAMP[5][0]
  const r = count / target
  return RAMP[r < 0.25 ? 1 : r < 0.5 ? 2 : r < 0.75 ? 3 : 4][0]
}

export function ProgressPanel({ base }: { base: string }) {
  const { data, error } = useLoad<Progress>(`${base}/progress`)
  const [order, setOrder] = useState<'name' | 'least'>('name')
  if (error) return <div className="error-box">{error}</div>
  if (!data) return null
  const scenes = [...data.scenes].sort((a, b) => (order === 'name' ? a.scene_id.localeCompare(b.scene_id)
    : a.at_target / a.items - b.at_target / b.items || a.min - b.min))
  return (
    <Panel title={`场景 × 人次（${data.scenes.length} 个场景）`} aside={
      <div className="seg">
        <button className={order === 'name' ? 'on' : ''} onClick={() => setOrder('name')}>按场景</button>
        <button className={order === 'least' ? 'on' : ''} onClick={() => setOrder('least')}>最缺的在前</button>
      </div>
    }>
      <div className="legend">
        <span>每格一道正式题，颜色是已完成人次占目标 {data.target} 人次的比例：</span>
        {RAMP.map(([color, label]) => <span key={label}><i style={{ background: color }} />{label}</span>)}
      </div>
      <div className="heat">
        {scenes.map((s) => (
          <div className="line" key={s.scene_id}>
            <span className="name"><span className="mono">{s.scene_id}</span>{s.core > 0 && <span className="badge plain">核心</span>}</span>
            <div className="cells">
              {s.counts.map((n, i) => <div key={i} className="cell" style={{ background: cellColor(n, data.target) }}
                title={`${s.scene_id} 第 ${i + 1} 题：${n} / ${data.target} 人次`} />)}
            </div>
            <div className="tail">
              <Meter value={s.at_target} max={s.items} />
              <span className="num">{s.at_target}/{s.items} 达标 · 最少 {s.min}</span>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------- raters

type Exclusion = Partial<Record<'gold_accuracy_min' | 'self_consistency_min' | 'swap_consistency_min' | 'rt_fast_share_max', number | null>>

const rate = (r: Rate) => (r.n ? `${pct(r.rate)}` : '—')
const below = (r: Rate, min?: number | null) => min != null && r.n > 0 && r.rate != null && r.rate < min

const COLUMNS: [string, string, (r: RaterCard) => number | string][] = [
  ['rater', '评分者', (r) => r.rater],
  ['n_main', '正式作答', (r) => r.n_main],
  ['self', '自一致', (r) => r.self_consistency.rate ?? -1],
  ['swap', '互换一致', (r) => r.swap_consistency.rate ?? -1],
  ['gold', '金标题', (r) => r.gold_accuracy.rate ?? -1],
  ['rt', '反应时中位', (r) => r.rt_median_ms ?? -1],
  ['fast', '过快占比', (r) => r.rt_fast_share ?? -1],
  ['left', '选左占比', (r) => r.left_share ?? -1],
]

export function RatersPanel({ base, exclusion }: { base: string; exclusion: Exclusion }) {
  const { data, error } = useLoad<RaterCard[]>(`${base}/raters`)
  const [only, setOnly] = useState(false)
  const [sort, setSort] = useState<{ key: string; desc: boolean }>({ key: 'rater', desc: false })
  const rows = useMemo(() => {
    if (!data) return []
    const get = COLUMNS.find(([k]) => k === sort.key)![2]
    const list = data.filter((r) => !only || r.excluded || (r.display && !r.display.passed))
    return list.sort((a, b) => {
      const x = get(a), y = get(b)
      const cmp = typeof x === 'string' ? x.localeCompare(String(y)) : x - (y as number)
      return sort.desc ? -cmp : cmp
    })
  }, [data, only, sort])
  if (error) return <div className="error-box">{error}</div>
  if (!data) return null
  if (!data.length) return <Panel><Empty title="还没有正式作答">评分者完成第一块正式题后，这里会出现质控指标。</Empty></Panel>
  const flagged = data.filter((r) => r.excluded).length
  const multiArm = new Set(data.map((r) => r.arm)).size > 1
  const header = (key: string, label: string) => (
    <th key={key} className={`sortable ${key === 'rater' ? '' : 'num'}`}
      onClick={() => setSort({ key, desc: sort.key === key ? !sort.desc : key !== 'rater' })}>
      {label}{sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : ''}
    </th>
  )
  return (
    <Panel flush title={`${data.length} 位评分者 · ${flagged} 位按规则标记排除`} aside={
      <div className="seg">
        <button className={only ? '' : 'on'} onClick={() => setOnly(false)}>全部</button>
        <button className={only ? 'on' : ''} onClick={() => setOnly(true)}>只看有问题的</button>
      </div>
    }>
      <p className="small muted" style={{ padding: '0 20px 12px' }}>
        排除规则来自冻结配置，这里只标记，不删除数据。
        {exclusion.gold_accuracy_min != null && ` 金标题 < ${pct(exclusion.gold_accuracy_min)}`}
        {exclusion.self_consistency_min != null && ` · 自一致 < ${pct(exclusion.self_consistency_min)}`}
        {exclusion.swap_consistency_min != null && ` · 互换一致 < ${pct(exclusion.swap_consistency_min)}`}
        {exclusion.rt_fast_share_max != null && ` · 过快占比 > ${pct(exclusion.rt_fast_share_max)}`}
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr>
            {COLUMNS.map(([k, label]) => header(k, label))}
            <th>显示</th><th>标记</th>
          </tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.rater} className={r.excluded ? 'flagged' : ''}>
              <td className="nowrap"><span className="mono">{r.rater}</span><span className="sub">{TIER[r.tier] ?? r.tier}{multiArm ? ` · ${r.arm}` : ''}</span></td>
              <td className="num">{r.n_main}</td>
              <td className={`num ${below(r.self_consistency, exclusion.self_consistency_min) ? 'fail' : ''}`}>
                {rate(r.self_consistency)}<span className="sub">n={r.self_consistency.n}</span></td>
              <td className={`num ${below(r.swap_consistency, exclusion.swap_consistency_min) ? 'fail' : ''}`}>
                {rate(r.swap_consistency)}<span className="sub">n={r.swap_consistency.n}</span></td>
              <td className={`num ${below(r.gold_accuracy, exclusion.gold_accuracy_min) ? 'fail' : ''}`}>
                {rate(r.gold_accuracy)}<span className="sub">n={r.gold_accuracy.n}</span></td>
              <td className="num">{r.rt_median_ms ? `${(r.rt_median_ms / 1000).toFixed(1)} 秒` : '—'}</td>
              <td className={`num ${exclusion.rt_fast_share_max != null && (r.rt_fast_share ?? 0) > exclusion.rt_fast_share_max ? 'fail' : ''}`}>
                {pct(r.rt_fast_share)}</td>
              <td className="num">{pct(r.left_share)}</td>
              <td>
                {r.display ? (
                  <>
                    <span className={`badge ${r.display.passed ? 'plain' : 'bad'}`}><i />{r.display.passed ? '通过' : '未通过'}</span>
                    <span className="sub nowrap">
                      {r.display.screen ? `${r.display.screen.width}×${r.display.screen.height}` : ''} @{r.display.dpr}× · 台阶 {r.display.black}/{r.display.white}
                    </span>
                  </>
                ) : '—'}
              </td>
              <td>
                <div className="reasons">
                  {r.exclusion_reasons.map((x) => <span key={x} className="badge bad"><i />{x}</span>)}
                  {r.display?.passed === false && r.display.reasons.map((x) => <span key={x} className="badge warn"><i />{x}</span>)}
                  {!r.excluded && r.display?.passed !== false && <span className="muted">—</span>}
                </div>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------- invites

export function InvitesPanel({ base, detail, reload }: { base: string; detail: Detail; reload: () => void }) {
  const tiers: string[] = detail.config.tiers ?? ['lab', 'remote']
  const [form, setForm] = useState({ tier: tiers[0], count: 1, max_uses: 1, arm: '', note: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const arms: { id: string }[] = detail.config.arms ?? []
  const origin = window.location.origin
  const create = async () => {
    setBusy(true)
    try {
      await post(`${base}/invites`, form)
      setError(null)
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const invites = [...detail.invites].reverse()
  const usable = detail.status === 'live'
  return (
    <Panel flush title="邀请链接" aside={invites.length > 0 &&
      <Copy text={invites.map((i) => origin + i.url).join('\n')} label="复制全部链接" />}>
      <div style={{ padding: '0 20px' }}>
        <div className="invite-form">
          <label className="field">档位
            <select value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })}>
              {tiers.map((t) => <option key={t} value={t}>{TIER[t] ?? t}档</option>)}
            </select>
          </label>
          {arms.length > 1 && (
            <label className="field">分组
              <select value={form.arm} onChange={(e) => setForm({ ...form, arm: e.target.value })}>
                <option value="">自动均衡</option>
                {arms.map((a) => <option key={a.id} value={a.id}>{a.id}</option>)}
              </select>
            </label>
          )}
          <label className="field">生成几个
            <input type="number" min={1} max={500} value={form.count} onChange={(e) => setForm({ ...form, count: Number(e.target.value) })} />
          </label>
          <label className="field">每个可用几人
            <input type="number" min={1} value={form.max_uses} onChange={(e) => setForm({ ...form, max_uses: Number(e.target.value) })} />
          </label>
          <label className="field" style={{ flex: 1, minWidth: 160 }}>备注
            <input placeholder="例如：第一批实验室被试" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </label>
          <button className="btn primary" disabled={busy || form.count < 1} onClick={create}>生成链接</button>
        </div>
        {error && <div className="error-box" style={{ marginBottom: 12 }}>{error}</div>}
        <p className="small muted" style={{ marginBottom: 12 }}>
          {!usable && '实验发布后链接才能使用。'}众包平台可在链接后加 <code>?src=平台&amp;pid=参与者编号</code>，评分者完成一块后会看到完成码。
        </p>
      </div>
      {invites.length === 0 ? <Empty title="还没有邀请链接" /> : (
        <table>
          <thead><tr><th>链接</th><th>档位</th><th>分组</th><th className="num">已用 / 上限</th><th>备注</th></tr></thead>
          <tbody>{invites.map((i) => (
            <tr key={i.url}>
              <td><div className="link"><code>{origin}{i.url}</code><Copy text={origin + i.url} /></div></td>
              <td>{TIER[i.tier] ?? i.tier}</td>
              <td>{i.arm ?? <span className="muted">自动</span>}</td>
              <td><div className="uses"><Meter value={i.uses} max={i.max_uses} /><span className="num small">{i.uses}/{i.max_uses}</span></div></td>
              <td className="muted">{i.note}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Panel>
  )
}

// ---------------------------------------------------------------- preview

interface Preview {
  template: string; arms: { id: string; question: string }[]; choices: string[] | null
  items: { item_key: string; kind: string; scene_id: string; images: { slot: number; main: { css_width: number; css_height: number; src: Record<string, string> } }[] }[]
}

const KIND: Record<string, string> = { main: '正式', practice: '练习', gold: '金标' }

export function PreviewPanel({ base }: { base: string }) {
  const { data, error } = useLoad<Preview>(`${base}/preview?n=6`)
  if (error) return <div className="error-box">{error}</div>
  if (!data) return null
  const scale = window.devicePixelRatio >= 1.75 ? '2' : window.devicePixelRatio >= 1.25 ? '1.5' : '1'
  return (
    <Panel title="评分端预览" aside={<span className="small muted">按评分端的 CSS 尺寸原样显示前几道题（放不下时横向滚动），不记录作答</span>}>
      <div className="question-box" style={{ marginBottom: 16 }}>
        {data.arms.map((a) => (
          <span key={a.id}>{data.arms.length > 1 && <span className="badge plain" style={{ marginRight: 8 }}>{a.id}</span>}<b>{a.question}</b></span>
        ))}
      </div>
      <div className="preview">
        {data.items.map((it) => (
          <div className="item" key={it.kind + it.item_key}>
            <header><span>{KIND[it.kind] ?? it.kind}</span><span>{it.scene_id}</span><span>{it.item_key}</span></header>
            <div className="imgs">
              {it.images.map((im) => <img key={im.slot} src={im.main.src[scale]} width={im.main.css_width} height={im.main.css_height}
                style={{ width: im.main.css_width, height: im.main.css_height }} alt="" />)}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------- config

const STRATEGY: Record<string, string> = {
  coverage: '覆盖', core_then_coverage: '核心集 → 覆盖', core_then_active: '核心集 → 主动采样',
}

export function ConfigPanel({ detail }: { detail: Detail }) {
  const c = detail.config
  const rows: [string, React.ReactNode][] = [
    ['模板', TEMPLATE[c.template] ?? c.template],
    ['问题', (c.arms ?? []).map((a: { id: string; question: string }) => <div key={a.id}>{c.arms.length > 1 && <b>{a.id}：</b>}{a.question}</div>)],
    ['作答档数', c.template === 'pair' ? `${c.response_scale} 档` : '—'],
    ['题量', `每题目标 ${c.target_per_item} 人次，每块 ${c.block_size} 题`],
    ['调度', `${STRATEGY[c.scheduler?.strategy] ?? c.scheduler?.strategy}；同场景至少间隔 ${c.scheduler?.scene_gap} 题`],
    ['质控插入', `隐藏重复 ${pct(c.qc?.hidden_repeat_rate)} · 左右互换 ${pct(c.qc?.swap_rate)} · 金标题 ${pct(c.qc?.gold_rate)}`
      + (c.qc?.gold_arms ? `（只计 ${c.qc.gold_arms.join('、')}）` : '')],
    ['B+ 追问', c.followup?.enabled ? `开启，抽取 ${pct(c.followup.rate)}${c.followup.non_tie_only ? '，只追问非平局' : ''}` : '关闭'],
    ['显示', `CSS 长边 ${c.display?.css_long_edge}${c.display?.zoom_css_long_edge ? `，放大 ${c.display.zoom_css_long_edge}` : ''}；`
      + `允许 dpr ${(c.display?.allowed_dpr ?? []).join(' / ')}；像素测试：${(c.display?.pixel_test_required ?? []).map((t: string) => TIER[t] ?? t).join('、') || '不要求'}`],
    ['档位', (c.tiers ?? []).map((t: string) => TIER[t] ?? t).join('、')],
    ['知情同意书', `版本 ${c.consent?.version}`],
  ]
  return (
    <Panel title="冻结配置">
      <dl className="config">
        {rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
      </dl>
      <details style={{ marginTop: 16 }}>
        <summary>完整 JSON</summary>
        <pre>{JSON.stringify(c, null, 2)}</pre>
      </details>
    </Panel>
  )
}

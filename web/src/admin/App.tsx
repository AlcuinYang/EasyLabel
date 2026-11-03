import { useEffect, useState } from 'react'
import { ApiError, post, request } from '../shared/api'
import { ExperimentPage } from './Experiment'
import type { ExperimentRow } from './types'
import { Empty, Meter, Panel, StatusBadge, TEMPLATE, relTime, useLoad } from './ui'

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
  const [signedIn, setSignedIn] = useState<boolean | null>(null)
  const hash = useHash()
  useEffect(() => {
    request('/api/admin/session').then(() => setSignedIn(true), () => setSignedIn(false))
    const out = () => setSignedIn(false)
    window.addEventListener('el-signed-out', out)
    return () => window.removeEventListener('el-signed-out', out)
  }, [])
  if (signedIn === null) return null
  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />
  const match = hash.match(/^#\/exp\/(.+)$/)
  const id = match ? decodeURIComponent(match[1]) : null
  const signOut = async () => {
    await request('/api/admin/session', { method: 'DELETE' }).catch(() => undefined)
    setSignedIn(false)
  }
  return (
    <>
      <header className="appbar">
        <a className="logo" href="#/"><i />EasyLabel</a>
        <nav className="crumbs">
          <span>/</span><a href="#/">实验</a>
          {id && <><span>/</span><span className="mono">{id}</span></>}
        </nav>
        <span className="spacer" />
        <button className="btn ghost sm" onClick={signOut}>退出</button>
      </header>
      <main>{id ? <ExperimentPage key={id} id={id} /> : <ExperimentList />}</main>
    </>
  )
}

function Login({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await post('/api/admin/session', { token })
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }
  return (
    <div className="login">
      <form onSubmit={submit}>
        <a className="logo" href="#/"><i />EasyLabel</a>
        <div>
          <h1>管理端登录</h1>
          <p className="muted small" style={{ marginTop: 6 }}>令牌是启动服务时设置的 EASYLABEL_ADMIN_TOKEN，服务启动时也会打印带令牌的登录链接。</p>
        </div>
        <label className="field">管理员令牌
          <input type="password" autoFocus autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        {error && <div className="error-box">{error}</div>}
        <button className="btn primary" type="submit" disabled={!token}>登录</button>
      </form>
    </div>
  )
}

function ExperimentList() {
  const { data, error } = useLoad<ExperimentRow[]>('/api/admin/experiments')
  if (error) return <div className="error-box">{error}</div>
  if (!data) return null
  const live = data.filter((e) => e.status === 'live').length
  return (
    <>
      <div className="page-head">
        <div>
          <h1>实验</h1>
          <p>{data.length} 个实验，{live} 个正在收集。新实验用命令行 <code>easylabel create-experiment</code> 导入配置。</p>
        </div>
      </div>
      <Panel flush={data.length > 0}>
        {data.length === 0 ? (
          <Empty title="还没有实验">写好 YAML 配置后运行 <code>easylabel --data DATA create-experiment config.yaml</code>。</Empty>
        ) : (
          <table>
              <thead><tr>
                <th>实验</th><th>状态</th><th>人次进度</th><th className="num">评分者</th><th className="num">作答</th><th>最近活动</th>
              </tr></thead>
              <tbody>{data.map((e) => {
                const goal = e.items * e.target
                return (
                  <tr key={e.id}>
                    <td className="title-cell">
                      <a href={`#/exp/${encodeURIComponent(e.id)}`}>{e.title}</a>
                      <span className="sub"><span className="mono">{e.id}</span> · {TEMPLATE[e.template] ?? e.template}</span>
                    </td>
                    <td><StatusBadge status={e.status} /></td>
                    <td>
                      <div className="progress-cell">
                        <Meter value={e.done} max={goal} />
                        <span className="small muted num">{goal ? Math.round((100 * e.done) / goal) : 0}%</span>
                      </div>
                      <span className="sub">{e.done} / {goal} 人次 · {e.items} 题 × {e.target}</span>
                    </td>
                    <td className="num">{e.raters}</td>
                    <td className="num">{e.answered}</td>
                    <td className="muted">{relTime(e.last_activity)}</td>
                  </tr>
                )
              })}</tbody>
          </table>
        )}
      </Panel>
    </>
  )
}

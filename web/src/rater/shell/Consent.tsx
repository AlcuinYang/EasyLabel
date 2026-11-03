import { useState } from 'react'
import type { StageProps } from './types'

export function Consent({ state, api, run, error }: StageProps) {
  const [declined, setDeclined] = useState(false)
  const consent = state.consent!
  if (declined) {
    return (
      <div className="page"><div className="card">
        <h1>已退出</h1>
        <p>你没有同意参加，本页不会记录任何作答。可以直接关闭页面。</p>
        <div className="row"><button onClick={() => setDeclined(false)}>返回</button></div>
      </div></div>
    )
  }
  return (
    <div className="page"><div className="card">
      <h1>{state.experiment.title}</h1>
      <p className="prose">{consent.text}</p>
      <p className="muted">知情同意书版本 {consent.version}</p>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="primary" onClick={() => run(() => api.consent(consent.version))}>同意并继续</button>
        <button onClick={() => setDeclined(true)}>不同意</button>
      </div>
    </div></div>
  )
}

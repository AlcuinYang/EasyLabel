import { useState } from 'react'
import { ErrorNote, Sheet, Shell } from './Shell'
import type { StageProps } from './types'

export function Consent({ state, api, run, error }: StageProps) {
  const [declined, setDeclined] = useState(false)
  const consent = state.consent!
  if (declined) {
    return (
      <Shell state={state}>
        <Sheet title="已退出" lead="你没有同意参加，本页不会记录任何作答，可以直接关闭页面。">
          <div className="actions"><button className="btn" onClick={() => setDeclined(false)}>返回同意书</button></div>
        </Sheet>
      </Shell>
    )
  }
  return (
    <Shell state={state}>
      <Sheet eyebrow="参加之前" title="知情同意" lead={state.experiment.title}>
        <div className="scroll"><p className="prose">{consent.text}</p></div>
        <ErrorNote error={error} />
        <div className="actions split">
          <span className="muted">同意书版本 {consent.version}</span>
          <div className="actions">
            <button className="btn ghost" onClick={() => setDeclined(true)}>不同意</button>
            <button className="btn primary lg" onClick={() => run(() => api.consent(consent.version))}>同意并继续</button>
          </div>
        </div>
      </Sheet>
    </Shell>
  )
}

import { useState, type ReactNode } from 'react'
import type { RaterState } from '../types'

const ORDER: RaterState['stage'][] = ['consent', 'profile', 'display', 'practice_intro', 'ready']

function steps(state: RaterState) {
  const names = ['知情同意', '基本信息', '显示检查', ...(state.has_practice ? ['练习'] : []), '正式标注']
  const stage = state.stage === 'task' || state.stage === 'finished' || state.stage === 'closed' ? 'ready' : state.stage
  let at = ORDER.indexOf(stage)
  if (!state.has_practice && at >= 3) at -= 1
  if (state.stage === 'finished' || state.stage === 'closed') at = names.length
  return { names, at }
}

export function Shell({ state, children }: { state?: RaterState; children: ReactNode }) {
  const flow = state ? steps(state) : null
  return (
    <div className="shell">
      <div className="topbar">
        <div className="brand"><b>EasyLabel</b>{state && <span>{state.experiment.title}</span>}</div>
        {flow && (
          <ol className="stepper" aria-label="进度">
            {flow.names.map((name, i) => (
              <li key={name} className={i < flow.at ? 'done' : i === flow.at ? 'now' : ''}
                aria-current={i === flow.at ? 'step' : undefined}>
                <i>{i < flow.at ? '✓' : i + 1}</i>{name}
              </li>
            ))}
          </ol>
        )}
      </div>
      <div className="page">{children}</div>
    </div>
  )
}

export function Sheet({ eyebrow, title, lead, wide, children }: {
  eyebrow?: string; title: string; lead?: ReactNode; wide?: boolean; children?: ReactNode
}) {
  return (
    <section className={`sheet ${wide ? 'wide' : ''}`}>
      <header>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {lead && <p className="lead">{lead}</p>}
      </header>
      {children}
    </section>
  )
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null
}

export function CompletionCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => setCopied(true), () => undefined)
  }
  return (
    <div className="code">
      <div><span className="muted">完成码</span><strong>{code}</strong></div>
      <button className="btn" onClick={copy}>{copied ? '已复制' : '复制'}</button>
    </div>
  )
}

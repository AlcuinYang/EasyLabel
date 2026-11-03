import { useEffect, useState } from 'react'
import type { TemplateProps } from '../TaskFrame'
import { PairImages } from './Pair'

const LABELS: Record<string, string> = {
  tone: '影调', color: '色彩', both: '两者都有', other: '其他（请写明）', cannot_tell: '看不出',
}

export function Followup(props: TemplateProps & { ready: boolean }) {
  const { task, enabled, submit } = props
  const choices = task.choices!
  const [other, setOther] = useState<string | null>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!enabled || other !== null) return
      const k = Number(e.key)
      if (!Number.isInteger(k) || k < 1 || k > choices.length) return
      const c = choices[k - 1]
      if (c === 'other') {
        e.preventDefault()
        setOther('')
      } else submit({ source: c })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled, submit, choices, other])
  return (
    <>
      <PairImages {...props} />
      <div className="answers">
        {other === null ? choices.map((c, i) => (
          <button key={c} disabled={!enabled}
            onClick={() => (c === 'other' ? setOther('') : submit({ source: c }))}><kbd>{i + 1}</kbd>{LABELS[c] ?? c}</button>
        )) : (
          <form className="row" onSubmit={(e) => { e.preventDefault(); if (other.trim()) submit({ source: 'other', other_text: other }) }}>
            <input type="text" autoFocus placeholder="差别来自哪里？" value={other} style={{ width: 360 }}
              onChange={(e) => setOther(e.target.value)} onKeyDown={(e) => e.stopPropagation()} />
            <button className="primary" type="submit" disabled={!enabled || !other.trim()}>提交</button>
            <button type="button" onClick={() => setOther(null)}>返回</button>
          </form>
        )}
      </div>
    </>
  )
}

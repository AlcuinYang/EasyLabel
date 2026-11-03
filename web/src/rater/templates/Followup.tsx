import { useEffect, useState } from 'react'
import type { TemplateProps } from '../TaskFrame'
import { PairImages } from './Pair'

const LABELS: Record<string, string> = {
  tone: '影调', color: '色彩', both: '两者都有', other: '其他', cannot_tell: '看不出',
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
      <div className="footer">
        {other === null ? (
          <div className="answers">
            {choices.map((c, i) => (
              <button key={c} className="answer" disabled={!enabled}
                onClick={() => (c === 'other' ? setOther('') : submit({ source: c }))}>{LABELS[c] ?? c}<kbd>{i + 1}</kbd></button>
            ))}
          </div>
        ) : (
          <form className="other-form" onSubmit={(e) => { e.preventDefault(); if (other.trim()) submit({ source: 'other', other_text: other }) }}>
            <input type="text" autoFocus placeholder="差别来自哪里？" value={other}
              onChange={(e) => setOther(e.target.value)} onKeyDown={(e) => e.stopPropagation()} />
            <button className="btn ghost" type="button" onClick={() => setOther(null)}>返回</button>
            <button className="btn primary" type="submit" disabled={!enabled || !other.trim()}>提交</button>
          </form>
        )}
        <div className="hint">这是你本块里判过的一对，左右位置和当时相同。</div>
      </div>
    </>
  )
}

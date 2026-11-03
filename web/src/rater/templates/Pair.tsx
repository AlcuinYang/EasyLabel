import { useEffect } from 'react'
import type { TemplateProps } from '../TaskFrame'

const LABELS: Record<string, string> = {
  left_strong: '明显左', left: '左', tie: '差不多', right: '右', right_strong: '明显右',
}

export function PairImages({ task, scale, ready }: Pick<TemplateProps, 'task' | 'scale'> & { ready: boolean }) {
  return (
    <div className="stage">
      <div className={`pair ${ready ? '' : 'hidden'}`}>
        {task.images.map((im) => (
          <img key={im.slot} src={im.main.src[scale]} width={im.main.css_width} height={im.main.css_height}
            style={{ width: im.main.css_width, height: im.main.css_height }} draggable={false} alt="" />
        ))}
      </div>
    </div>
  )
}

export function Pair(props: TemplateProps & { ready: boolean }) {
  const { task, enabled, submit } = props
  const choices = task.choices!
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = Number(e.key)
      if (enabled && Number.isInteger(k) && k >= 1 && k <= choices.length) submit({ choice: choices[k - 1] })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled, submit, choices])
  return (
    <>
      <PairImages {...props} />
      <div className="footer">
        <div className="answers">
          {choices.map((c, i) => (
            <button key={c} className="answer" disabled={!enabled} onClick={() => submit({ choice: c })}>
              {LABELS[c] ?? c}<kbd>{i + 1}</kbd>
            </button>
          ))}
        </div>
      </div>
    </>
  )
}

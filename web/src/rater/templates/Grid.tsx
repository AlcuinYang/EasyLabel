import { useEffect, useState } from 'react'
import { gridCell } from '../display'
import type { TemplateProps } from '../TaskFrame'

export function Grid({ task, scale, enabled, submit, log, ready }: TemplateProps & { ready: boolean }) {
  const [step, setStep] = useState<'accept' | 'best'>('accept')
  const [accepted, setAccepted] = useState<number[]>([])
  const [none, setNone] = useState(false)
  const [best, setBest] = useState<number | null>(null)
  const [zoom, setZoom] = useState<number | null>(null)
  const cell = gridCell(task.images)
  const active = enabled && zoom === null

  const toggle = (slot: number) => {
    setNone(false)
    setAccepted((a) => (a.includes(slot) ? a.filter((s) => s !== slot) : [...a, slot].sort()))
  }
  const candidate = (slot: number) => none || accepted.length === 0 || accepted.includes(slot)
  const canNext = none || accepted.length > 0
  const done = () => best !== null && submit({ accepted, none, best })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (zoom !== null && e.key === 'Escape') { e.stopPropagation(); setZoom(null); return }
      if (!active) return
      const k = Number(e.key)
      if (e.key === 'Enter') e.preventDefault() // never also activate the focused button
      if (step === 'accept') {
        if (Number.isInteger(k) && k >= 1 && k <= task.images.length) toggle(k - 1)
        else if (e.key === '0') { setNone((n) => !n); setAccepted([]) }
        else if (e.key === 'Enter' && canNext) setStep('best')
      } else {
        if (Number.isInteger(k) && k >= 1 && k <= task.images.length && candidate(k - 1)) setBest(k - 1)
        else if (e.key === 'ArrowLeft') { setStep('accept'); setBest(null) }
        else if (e.key === 'Enter') done()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  const zoomed = zoom === null ? null : task.images[zoom]
  const zsize = zoomed ? (zoomed.zoom ?? zoomed.main) : null
  return (
    <>
      <div className="stage">
        <div className={`grid ${ready ? '' : 'hidden'}`}>
          {task.images.map((im, i) => {
            const cls = ['tile', step === 'accept' && accepted.includes(i) ? 'accepted' : '',
              step === 'best' && best === i ? 'best' : '', step === 'best' && accepted.includes(i) ? 'accepted' : ''].join(' ')
            return (
              <div className={cls} key={im.slot}>
                <div className="frame" style={{ width: cell.width, height: cell.height }}
                  onClick={() => { if (enabled) { setZoom(i); log('zoom', { slot: i }) } }}>
                  <img src={im.main.src[scale]} width={im.main.css_width} height={im.main.css_height}
                    style={{ width: im.main.css_width, height: im.main.css_height }} draggable={false} alt="" />
                </div>
                {step === 'accept'
                  ? <button disabled={!active} onClick={() => toggle(i)}><kbd>{i + 1}</kbd>{accepted.includes(i) ? '已达到 ✓' : '达到'}</button>
                  : <button disabled={!active || !candidate(i)} onClick={() => setBest(i)}><kbd>{i + 1}</kbd>{best === i ? '最好 ✓' : '最好'}</button>}
              </div>
            )
          })}
        </div>
      </div>
      <div className="answers">
        {step === 'accept' ? (
          <>
            <span className="muted" style={{ alignSelf: 'center' }}>勾选所有已达到应有样子的版本（可多选）；点图可放大</span>
            <button disabled={!active} className={none ? 'selected' : ''} onClick={() => { setNone(!none); setAccepted([]) }}>
              <kbd>0</kbd>{none ? '都没有 ✓' : '都没有'}
            </button>
            <button className="primary" disabled={!active || !canNext} onClick={() => setStep('best')}><kbd>Enter</kbd>下一步</button>
          </>
        ) : (
          <>
            <span className="muted" style={{ alignSelf: 'center' }}>再挑出最好的一张{accepted.length ? '（在已勾选的版本中）' : ''}</span>
            <button disabled={!active} onClick={() => { setStep('accept'); setBest(null) }}><kbd>←</kbd>返回</button>
            <button className="primary" disabled={!active || best === null} onClick={done}><kbd>Enter</kbd>提交</button>
          </>
        )}
      </div>
      {zoomed && zsize && (
        <div className="overlay zoom" onClick={() => setZoom(null)}>
          <img src={zsize.src[scale]} width={zsize.css_width} height={zsize.css_height}
            style={{ width: zsize.css_width, height: zsize.css_height, maxWidth: 'none' }} draggable={false} alt="" />
        </div>
      )}
    </>
  )
}

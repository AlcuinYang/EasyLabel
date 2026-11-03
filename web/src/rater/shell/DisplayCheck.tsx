import { useEffect, useRef, useState } from 'react'
import { currentEnv } from '../display'
import { ErrorNote, Sheet, Shell } from './Shell'
import type { StageProps } from './types'

const BLACK = [0, 3, 6, 9, 12]
const WHITE = [243, 246, 249, 252, 255]
const PANEL = { w: 160, h: 96 } // CSS px; ×1.5 and ×2 are integers, so device pixels map 1:1

type Step = 'black' | 'white' | 'pixel' | 'submit'

export function DisplayCheck({ state, api, run, error }: StageProps) {
  const needPixel = state.display!.pixel_test
  const [step, setStep] = useState<Step>('black')
  const [answers, setAnswers] = useState<{ black?: number; white?: number; pixel?: string }>({})
  const [pending, setPending] = useState(false)
  const result = state.display_result
  const total = needPixel ? 3 : 2

  const submit = (pixel: string, given = answers) => {
    const env = currentEnv()
    try { sessionStorage.setItem('el-dpr', String(env.dpr)) } catch { /* private mode */ }
    const payload = {
      ...env,
      black_steps_seen: given.black,
      white_steps_seen: given.white,
      pixel_test: pixel,
      screen: { width: screen.width, height: screen.height, avail_width: screen.availWidth, avail_height: screen.availHeight },
      outer_width: window.outerWidth,
      outer_height: window.outerHeight,
      color_gamut: ['rec2020', 'p3', 'srgb'].find((g) => matchMedia(`(color-gamut: ${g})`).matches) ?? null,
    }
    setStep('submit')
    setPending(true)
    void run(() => api.displayCheck(payload)).finally(() => setPending(false))
  }

  const count = (n: number) => {
    if (step !== 'black' && step !== 'white') return
    const next = { ...answers, [step]: n }
    setAnswers(next)
    if (step === 'black') setStep('white')
    else if (needPixel) setStep('pixel')
    else submit('skipped', next)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = Number(e.key)
      if ((step === 'black' || step === 'white') && Number.isInteger(k) && k >= 1 && k <= 5) count(k)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (step === 'submit' && !pending && result && !result.passed) {
    return (
      <Shell state={state}>
        <Sheet eyebrow="显示检查" title="显示检查未通过" lead="这台设备目前的设置不能保证图片按原始像素准确显示：">
          <ul className="list">{result.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <div className="scroll">
            <p className="lead" style={{ marginBottom: 8 }}><b>怎么调整</b></p>
            <ul className="list muted" style={{ fontSize: 14 }}>
              <li>优先使用 4K 显示器。</li>
              <li>Windows：系统“缩放与布局”用 100% 或 150%。</li>
              <li>macOS：“显示器”设置里选默认分辨率（4K 屏上是“看起来像 1920 × 1080”），其他档位会重采样。</li>
              <li>浏览器缩放保持 100%（⌘ / Ctrl + 0）。</li>
              <li>调亮屏幕、避开强光直射，再数一次台阶。</li>
            </ul>
          </div>
          <div className="actions">
            <button className="btn primary lg" onClick={() => { setAnswers({}); setStep('black') }}>重新检查</button>
          </div>
        </Sheet>
      </Shell>
    )
  }

  if (step === 'black' || step === 'white') {
    const levels = step === 'black' ? BLACK : WHITE
    const n = step === 'black' ? 1 : 2
    return (
      <Shell state={state}>
        <Sheet eyebrow={`显示检查 · ${n} / ${total}`} title={step === 'black' ? '近黑台阶' : '近白台阶'}
          lead="下面这一条从左到右逐级变亮。数一数：你能彼此区分开的有几级？">
          <div className="check-figure">
            <div className="steps">{levels.map((v) => <div key={v} style={{ background: `rgb(${v},${v},${v})` }} />)}</div>
          </div>
          <div className="count">
            {[1, 2, 3, 4, 5].map((k) => (
              <button key={k} className="btn" onClick={() => count(k)}>{k} 级</button>
            ))}
          </div>
          <p className="muted">看不出差别就选 1 级。也可以直接按数字键 1–5。</p>
        </Sheet>
      </Shell>
    )
  }

  if (step === 'pixel') {
    return (
      <Shell state={state}>
        <Sheet eyebrow={`显示检查 · 3 / ${total}`} title="像素图案"
          lead="屏幕不缩放时，下面三块应当都是均匀的细纹理（远看接近灰色）。凑近看：有没有粗条带、波纹，或者忽深忽浅的区域？">
          <div className="pixel-tests">
            <Grating kind="alternate" label="竖条 1:1" /><Grating kind="third" label="竖条 1:2" /><Grating kind="checker" label="棋盘" />
          </div>
          <div className="stack">
            <button className="btn" onClick={() => submit('uniform')}>没有，都很均匀</button>
            <button className="btn" onClick={() => submit('banding')}>有条带或波纹</button>
            <button className="btn" onClick={() => submit('unsure')}>看不清 / 不确定</button>
          </div>
        </Sheet>
      </Shell>
    )
  }

  return (
    <Shell state={state}>
      <Sheet eyebrow="显示检查" title="正在提交…">
        <ErrorNote error={error} />
      </Sheet>
    </Shell>
  )
}

function Grating({ kind, label }: { kind: 'alternate' | 'third' | 'checker'; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current!
    const dpr = window.devicePixelRatio
    canvas.width = Math.round(PANEL.w * dpr)
    canvas.height = Math.round(PANEL.h * dpr)
    const ctx = canvas.getContext('2d')!
    const img = ctx.createImageData(canvas.width, canvas.height)
    for (let y = 0; y < canvas.height; y++) {
      for (let x = 0; x < canvas.width; x++) {
        const on = kind === 'alternate' ? x % 2 === 0 : kind === 'third' ? x % 3 === 0 : (x + y) % 2 === 0
        const i = (y * canvas.width + x) * 4
        img.data[i] = img.data[i + 1] = img.data[i + 2] = on ? 255 : 0
        img.data[i + 3] = 255
      }
    }
    ctx.putImageData(img, 0, 0)
  }, [kind])
  return (
    <figure>
      <canvas ref={ref} style={{ width: PANEL.w, height: PANEL.h }} />
      <figcaption>{label}</figcaption>
    </figure>
  )
}

import { useEffect, useRef, useState } from 'react'
import { currentEnv } from '../display'
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

  if (step === 'submit' && !pending && result && !result.passed) {
    return (
      <div className="page"><div className="card">
        <h1>显示检查未通过</h1>
        <ul className="reasons">{result.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <p className="muted">调整后重新检查。建议使用 4K 显示器，Windows 系统缩放用 100% 或 150%；macOS 在“显示器”设置里选默认分辨率（4K 屏上为“看起来像 1920×1080”），其他档位会重采样；浏览器缩放保持 100%。</p>
        <div className="row"><button className="primary" onClick={() => { setAnswers({}); setStep('black') }}>重新检查</button></div>
      </div></div>
    )
  }

  if (step === 'black' || step === 'white') {
    const levels = step === 'black' ? BLACK : WHITE
    return (
      <div className="page"><div className="card">
        <h1>显示检查 · {step === 'black' ? '近黑' : '近白'}台阶</h1>
        <p>下面这一条从左到右逐级变亮。请数一数：你能彼此区分开的有几级？</p>
        <div className="steps">{levels.map((v) => <div key={v} style={{ background: `rgb(${v},${v},${v})` }} />)}</div>
        <div className="row">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} onClick={() => {
              const next = { ...answers, [step]: n }
              setAnswers(next)
              if (step === 'black') setStep('white')
              else if (needPixel) setStep('pixel')
              else submit('skipped', next)
            }}>{n} 级</button>
          ))}
        </div>
      </div></div>
    )
  }

  if (step === 'pixel') {
    return (
      <div className="page"><div className="card">
        <h1>显示检查 · 像素图案</h1>
        <p>下面三块图案在屏幕不缩放时，看起来是均匀的细纹理（可能接近灰色）。凑近看：是否有粗条带、波纹或忽深忽浅的区域？</p>
        <div className="pixel-tests">
          <Grating kind="alternate" /><Grating kind="third" /><Grating kind="checker" />
        </div>
        <div className="row">
          <button onClick={() => submit('uniform')}>没有，都很均匀</button>
          <button onClick={() => submit('banding')}>有条带或波纹</button>
          <button onClick={() => submit('unsure')}>看不清 / 不确定</button>
        </div>
      </div></div>
    )
  }

  return (
    <div className="page"><div className="card">
      <h1>正在提交显示检查…</h1>
      {error && <p className="error">{error}</p>}
    </div></div>
  )
}

function Grating({ kind }: { kind: 'alternate' | 'third' | 'checker' }) {
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
  return <canvas ref={ref} style={{ width: PANEL.w, height: PANEL.h }} />
}

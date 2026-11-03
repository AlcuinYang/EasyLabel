import { useEffect, useRef, useState } from 'react'
import type { TemplateProps } from '../TaskFrame'

const TYPE_LABELS: Record<string, string> = {
  tone: '明暗层次', separation: '色彩分离', disharmony: '颜色不协调', other: '其他', unsure: '说不清',
}
const GLOBAL = 'global'

interface Mark { region: string; type?: string; direction?: 'down' | 'up'; other_text?: string }
interface LabelMap { width: number; height: number; labels: Uint8Array; centroids: Map<string, [number, number]> }

async function loadLabels(url: string): Promise<LabelMap> {
  const img = new Image()
  img.src = url
  await img.decode()
  const canvas = document.createElement('canvas')
  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!
  ctx.drawImage(img, 0, 0)
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
  const labels = new Uint8Array(canvas.width * canvas.height)
  const sums = new Map<number, [number, number, number]>()
  for (let i = 0; i < labels.length; i++) {
    const v = data[i * 4]
    labels[i] = v
    const s = sums.get(v) ?? [0, 0, 0]
    s[0] += i % canvas.width; s[1] += Math.floor(i / canvas.width); s[2] += 1
    sums.set(v, s)
  }
  const centroids = new Map<string, [number, number]>()
  sums.forEach(([x, y, n], id) => centroids.set(String(id), [x / n / canvas.width, y / n / canvas.height]))
  return { width: canvas.width, height: canvas.height, labels, centroids }
}

export function Region({ task, scale, enabled, submit, ready }: TemplateProps & { ready: boolean }) {
  const im = task.images[0].main
  const levels = task.levels ?? 5
  const maxRegions = task.max_regions ?? 3
  const [step, setStep] = useState<'overall' | 'regions'>('overall')
  const [overall, setOverall] = useState<number | null>(null)
  const [marks, setMarks] = useState<Mark[]>([])
  const [hover, setHover] = useState<string | null>(null)
  const [map, setMap] = useState<LabelMap | null>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => { void loadLabels(task.label_map!).then(setMap) }, [task.label_map])

  // Overlay: thin achromatic boundaries, selected regions lightly filled and numbered.
  useEffect(() => {
    const c = canvas.current
    if (!c || !map) return
    const dpr = window.devicePixelRatio
    c.width = Math.round(im.css_width * dpr)
    c.height = Math.round(im.css_height * dpr)
    const ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, c.width, c.height)
    if (step !== 'regions') return
    const layer = new ImageData(map.width, map.height)
    const selected = new Set(marks.map((m) => m.region))
    for (let y = 0; y < map.height; y++) {
      for (let x = 0; x < map.width; x++) {
        const i = y * map.width + x
        const v = map.labels[i]
        const id = String(v)
        const edge = (x + 1 < map.width && map.labels[i + 1] !== v) || (y + 1 < map.height && map.labels[i + map.width] !== v)
        const alpha = edge ? 150 : selected.has(id) ? 80 : hover === id ? 40 : 0
        layer.data[i * 4] = layer.data[i * 4 + 1] = layer.data[i * 4 + 2] = 255
        layer.data[i * 4 + 3] = alpha
      }
    }
    const tmp = document.createElement('canvas')
    tmp.width = map.width
    tmp.height = map.height
    tmp.getContext('2d')!.putImageData(layer, 0, 0)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(tmp, 0, 0, c.width, c.height)
    ctx.font = `600 ${18 * dpr}px system-ui`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    marks.forEach((m, k) => {
      const at = map.centroids.get(m.region)
      if (!at) return
      const [x, y] = [at[0] * c.width, at[1] * c.height]
      ctx.fillStyle = '#111'
      ctx.beginPath(); ctx.arc(x, y, 14 * dpr, 0, Math.PI * 2); ctx.fill()
      ctx.fillStyle = '#f2f2f2'
      ctx.fillText(String(k + 1), x, y)
    })
  }, [map, marks, hover, step, im])

  const regionAt = (e: React.MouseEvent) => {
    if (!map) return null
    const rect = e.currentTarget.getBoundingClientRect()
    const x = Math.min(map.width - 1, Math.floor(((e.clientX - rect.left) / rect.width) * map.width))
    const y = Math.min(map.height - 1, Math.floor(((e.clientY - rect.top) / rect.height) * map.height))
    return String(map.labels[y * map.width + x])
  }
  const toggle = (region: string) => setMarks((ms) => ms.some((m) => m.region === region)
    ? ms.filter((m) => m.region !== region)
    : ms.length < maxRegions ? [...ms, { region }] : ms)
  const update = (k: number, patch: Partial<Mark>) => setMarks((ms) => ms.map((m, i) => (i === k ? { ...m, ...patch } : m)))
  const complete = marks.length > 0 && marks.every((m) => m.type && (m.type === 'unsure' || m.direction)
    && (m.type !== 'other' || m.other_text?.trim()))
  const send = () => complete && submit({ overall, regions: marks })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!enabled || (e.target instanceof HTMLInputElement)) return
      const k = Number(e.key)
      if (step === 'overall' && Number.isInteger(k) && k >= 1 && k <= levels) {
        setOverall(k)
        setStep('regions')
      } else if (step === 'regions' && e.key === 'Enter') {
        e.preventDefault()
        send()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <>
      <div className="stage">
        <div className={`region ${ready ? '' : 'hidden'}`}>
          <div className="canvas-wrap" style={{ width: im.css_width, height: im.css_height }}>
            <img src={im.src[scale]} width={im.css_width} height={im.css_height}
              style={{ width: im.css_width, height: im.css_height }} draggable={false} alt="" />
            <canvas ref={canvas} style={{ width: im.css_width, height: im.css_height, pointerEvents: step === 'regions' ? 'auto' : 'none' }}
              onMouseMove={(e) => setHover(regionAt(e))} onMouseLeave={() => setHover(null)}
              onClick={(e) => { const r = regionAt(e); if (enabled && r !== null) toggle(r) }} />
          </div>
          <div className="panel">
            {step === 'overall' ? (
              <p className="muted">先给整张图打分（数字键 1–{levels}）。</p>
            ) : (
              <>
                <p className="muted">整图 {overall} 分。在图上点出最多 {maxRegions} 块需要改的区域，为每块选类型和方向；也可以选“全局 / 说不清位置”。</p>
                <button disabled={!enabled} className={marks.some((m) => m.region === GLOBAL) ? 'selected' : ''}
                  onClick={() => toggle(GLOBAL)}>全局 / 说不清位置</button>
                {marks.map((m, k) => (
                  <div className="mark" key={m.region}>
                    <div className="head"><span>{k + 1}. {m.region === GLOBAL ? '全局 / 说不清位置' : `区域 ${k + 1}`}</span>
                      <button onClick={() => toggle(m.region)} style={{ padding: '2px 8px' }}>移除</button></div>
                    <div className="chips">{(task.region_types ?? []).map((t) => (
                      <button key={t} className={m.type === t ? 'on' : ''} onClick={() => update(k, { type: t })}>{TYPE_LABELS[t] ?? t}</button>
                    ))}</div>
                    {m.type && m.type !== 'unsure' && (
                      <div className="chips">
                        <button className={m.direction === 'down' ? 'on' : ''} onClick={() => update(k, { direction: 'down' })}>该拉低</button>
                        <button className={m.direction === 'up' ? 'on' : ''} onClick={() => update(k, { direction: 'up' })}>该拉高</button>
                      </div>
                    )}
                    {m.type === 'other' && (
                      <input type="text" placeholder="请写明" value={m.other_text ?? ''}
                        onChange={(e) => update(k, { other_text: e.target.value })} />
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
      <div className="answers">
        {step === 'overall' ? (
          <div className="scale">
            <span className="muted">{task.low_label}</span>
            {Array.from({ length: levels }, (_, i) => i + 1).map((n) => (
              <button key={n} disabled={!enabled} onClick={() => { setOverall(n); setStep('regions') }}><kbd>{n}</kbd></button>
            ))}
            <span className="muted">{task.high_label}</span>
          </div>
        ) : (
          <>
            <button disabled={!enabled} onClick={() => { setStep('overall'); setMarks([]) }}>重新打分</button>
            <button className="primary" disabled={!enabled || !complete} onClick={send}><kbd>Enter</kbd>提交</button>
          </>
        )}
      </div>
    </>
  )
}

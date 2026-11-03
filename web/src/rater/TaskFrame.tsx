import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { currentEnv, fits, gridNeed, pairNeed, pickScale, regionNeed, type Need } from './display'
import { Sheet } from './shell/Shell'
import type { StageProps } from './shell/types'
import { Pair } from './templates/Pair'
import { Grid } from './templates/Grid'
import { Followup } from './templates/Followup'
import { Region } from './templates/Region'
import type { Scale } from './types'

const BLANK_MS = 250
const FEEDBACK_MS = 900
const FEEDBACK_TEXT = { left: '左', right: '右', tie: '差不多' }

export interface TemplateProps {
  task: NonNullable<StageProps['state']['task']>
  scale: Scale
  enabled: boolean
  submit: (payload: unknown) => void
  log: (type: string, payload?: Record<string, unknown>) => void
}

function storedDpr(): number | null {
  try {
    const v = sessionStorage.getItem('el-dpr')
    return v ? Number(v) : null
  } catch {
    return null
  }
}

function useWindowSize(): Need {
  const [size, setSize] = useState({ width: window.innerWidth, height: window.innerHeight })
  useEffect(() => {
    const on = () => setSize({ width: window.innerWidth, height: window.innerHeight })
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return size
}

export function TaskFrame({ state, api, run }: StageProps) {
  const task = state.task!
  const block = state.block!
  const win = useWindowSize()
  const dpr = window.devicePixelRatio
  const { scale } = pickScale(dpr)
  const checkedDpr = storedDpr()
  const zoomChanged = checkedDpr !== null && Math.abs(checkedDpr - dpr) > 0.01
  const need = useMemo(() => (task.template === 'grid' ? gridNeed(task.images)
    : task.template === 'region' ? regionNeed(task.images) : pairNeed(task.images)), [task])
  const tooSmall = !fits(need, win)

  const [ready, setReady] = useState(false)
  const [feedback, setFeedback] = useState(state.feedback ?? null)
  const [paused, setPaused] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const shownAt = useRef(0)
  const loggedBlock = useRef(false)

  const log = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    void api.event(type, payload, task.token)
  }, [api, task.token])

  // Preload every image at the chosen scale, then reveal all at once after a blank interval.
  useEffect(() => {
    let cancelled = false
    const started = performance.now()
    const loads = task.images.map((im) => {
      const img = new Image()
      img.src = im.main.src[scale]
      return img.decode().catch(() => undefined)
    })
    void Promise.all(loads).then(() => {
      const wait = Math.max(state.feedback ? FEEDBACK_MS : BLANK_MS, 0) - (performance.now() - started)
      setTimeout(() => {
        if (cancelled) return
        setFeedback(null)
        setReady(true)
        shownAt.current = performance.now()
      }, Math.max(0, wait))
    })
    return () => { cancelled = true }
  }, [task, scale, state.feedback])

  const blocked = tooSmall || zoomChanged
  useEffect(() => {
    if (blocked && !loggedBlock.current) {
      loggedBlock.current = true
      log('viewport_blocked', { ...currentEnv(), need, zoom_changed: zoomChanged })
    }
    if (blocked) shownAt.current = 0
    else if (ready && !shownAt.current) shownAt.current = performance.now()
  }, [blocked, ready, need, zoomChanged, log])

  const submit = useCallback((payload: unknown) => {
    if (busy || !shownAt.current) return
    setBusy(true)
    const rt = Math.round(performance.now() - shownAt.current)
    void run(() => api.answer({ token: task.token, payload, rt_ms: rt, env: { ...currentEnv(), scale } }))
      .finally(() => setBusy(false))
  }, [api, run, task.token, scale, busy])

  const enabled = ready && !blocked && !paused && note === null && !busy

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (note !== null) return
      if (e.key === 'Escape') {
        setPaused((p) => {
          log(p ? 'resume' : 'pause')
          if (p) shownAt.current = performance.now() // RT restarts after a pause
          return !p
        })
      } else if (paused) {
        return
      } else if (e.key === 'Backspace' && block.can_undo && !busy) {
        e.preventDefault()
        void run(api.undo)
      } else if ((e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey) {
        e.preventDefault()
        setNote('')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [note, paused, block.can_undo, busy, api, run, log])

  const undo = () => { if (block.can_undo && !busy) void run(api.undo) }
  const pause = () => { setPaused(true); log('pause') }
  const resume = () => { setPaused(false); log('resume'); shownAt.current = performance.now() }
  const where = block.kind === 'practice' ? '练习' : `第 ${block.number} 块`
  const position = Math.min(block.answered + 1, block.total)
  const props: TemplateProps = { task, scale, enabled, submit, log }

  return (
    <div className="task">
      <div className="taskbar">
        <span className="where">{where}{task.role === 'followup' ? ' · 追问' : ''}</span>
        <div className="meter" aria-label={`${where} 第 ${position} 题，共 ${block.total} 题`}>
          <div className="track"><div className="fill" style={{ width: `${(100 * block.answered) / Math.max(1, block.total)}%` }} /></div>
          <span className="pos">{position} / {block.total}</span>
        </div>
        <div className="tools">
          <button className="tool" disabled={!block.can_undo || busy || paused} onClick={undo}>撤回<kbd>⌫</kbd></button>
          <button className="tool" disabled={paused} onClick={() => setNote('')}>备注<kbd>N</kbd></button>
          <button className="tool" disabled={paused} onClick={pause}>暂停<kbd>Esc</kbd></button>
        </div>
      </div>
      <div className="question">{task.question}</div>
      {task.template === 'grid' ? <Grid {...props} ready={ready} />
        : task.template === 'followup' ? <Followup {...props} ready={ready} />
        : task.template === 'region' ? <Region {...props} ready={ready} />
        : <Pair {...props} ready={ready} />}

      {feedback && (
        <div className="toast" role="status">
          {feedback.correct ? '✓ 与参考答案一致' : `参考答案：${FEEDBACK_TEXT[feedback.expected]}`}
        </div>
      )}
      {blocked && (
        <div className="overlay">
          {zoomChanged ? (
            <Sheet eyebrow="题目已暂停显示" title="浏览器缩放改变了"
              lead="请把浏览器缩放恢复到 100%（⌘ / Ctrl + 0），图片才会按原始像素显示。恢复后题目会自动出现。" />
          ) : (
            <Sheet eyebrow="题目已暂停显示" title="窗口太小"
              lead={<>这道题需要至少 <b>{need.width} × {need.height}</b> 的窗口，当前是 {win.width} × {win.height}。</>}>
              <p className="lead">请放大或全屏浏览器窗口（macOS：⌃⌘F；Windows：F11）。不要用浏览器缩放来缩小页面。</p>
            </Sheet>
          )}
        </div>
      )}
      {paused && (
        <div className="overlay">
          <Sheet title="已暂停" lead="准备好后继续。暂停期间不计时。">
            <div className="actions"><button className="btn primary lg" onClick={resume}>继续<kbd>Esc</kbd></button></div>
          </Sheet>
        </div>
      )}
      {note !== null && (
        <div className="overlay">
          <Sheet title="备注" lead="记下这道题让你犹豫或觉得异常的地方（可选）。备注不影响作答。">
            <textarea rows={4} autoFocus value={note} onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setNote(null) } }} />
            <div className="actions">
              <button className="btn ghost" onClick={() => setNote(null)}>取消</button>
              <button className="btn primary" disabled={!note.trim()} onClick={() => { log('note', { text: note }); setNote(null) }}>保存</button>
            </div>
          </Sheet>
        </div>
      )}
    </div>
  )
}

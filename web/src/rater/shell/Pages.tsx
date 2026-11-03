import type { RaterState } from '../types'
import { CompletionCode, ErrorNote, Sheet, Shell } from './Shell'
import type { StageProps } from './types'

export function Message({ state, title, text, code }: { state?: RaterState; title: string; text: string; code?: string | null }) {
  return (
    <Shell state={state}>
      <Sheet title={title} lead={text || undefined}>
        {code && <CompletionCode code={code} />}
        {state && (
          <p className="muted">
            你的个人页面：<a href="/me">/me</a>。换浏览器或下次回来时，用当初的邀请链接或个人链接进入。
          </p>
        )}
      </Sheet>
    </Shell>
  )
}

function KeyGuide({ template }: { template: string }) {
  const answer = template === 'grid' ? '数字键勾选版本，Enter 进入下一步'
    : template === 'region' ? '数字键给整图打分，然后在图上点选区域'
      : '数字键作答：1 左 · 2 差不多 · 3 右（五档时为 1–5）'
  return (
    <div className="keys">
      <kbd>1–9</kbd><span>{answer}</span>
      <kbd>Backspace</kbd><span>撤回上一题（只限当前块）</span>
      <kbd>Esc</kbd><span>暂停，暂停期间不计时</span>
      <kbd>N</kbd><span>给当前这道题写备注</span>
    </div>
  )
}

export function PracticeIntro({ state, api, run, error }: StageProps) {
  return (
    <Shell state={state}>
      <Sheet eyebrow="练习" title="先做几道练习题"
        lead="练习题用来熟悉界面和按键。只有练习题会在作答后告诉你参考答案；正式题没有对错反馈，按你自己的判断作答即可。">
        <KeyGuide template={state.experiment.template} />
        <p className="muted">请保持浏览器缩放为 100%，作答过程中不要拖动或缩放窗口。</p>
        <ErrorNote error={error} />
        <div className="actions">
          <button className="btn primary lg" onClick={() => run(api.startBlock)}>开始练习</button>
        </div>
      </Sheet>
    </Shell>
  )
}

export function Ready({ state, api, run, error }: StageProps) {
  const done = state.blocks_done ?? 0
  return (
    <Shell state={state}>
      <Sheet eyebrow={done ? '休息一下' : '正式标注'} title={done ? `第 ${done} 块已完成` : '准备开始'}
        lead={done ? '可以继续下一块，也可以今天先到这里。下次用个人链接回来，会从新的一块开始。'
          : '正式题没有对错反馈。看清楚再答，不必追求速度。'}>
        <div className="facts">
          <div className="fact"><span className="muted">每块题数</span><strong>约 {state.block_size}</strong></div>
          <div className="fact"><span className="muted">中途离开</span><strong>块与块之间可停</strong></div>
          <div className="fact"><span className="muted">已完成</span><strong>{done} 块</strong></div>
        </div>
        {state.completion_code && <CompletionCode code={state.completion_code} />}
        {!done && <KeyGuide template={state.experiment.template} />}
        <ErrorNote error={error} />
        <div className="actions">
          {done > 0 && <a className="btn ghost" href="/me">今天到此为止</a>}
          <button className="btn primary lg" onClick={() => run(api.startBlock)}>{done ? '继续下一块' : '开始第 1 块'}</button>
        </div>
      </Sheet>
    </Shell>
  )
}

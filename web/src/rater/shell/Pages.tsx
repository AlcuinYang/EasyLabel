import type { StageProps } from './types'

export function Message({ title, text, code }: { title: string; text: string; code?: string | null }) {
  return (
    <div className="page"><div className="card">
      <h1>{title}</h1>
      {text && <p>{text}</p>}
      {code && <p>完成码：<strong>{code}</strong></p>}
    </div></div>
  )
}

export function PracticeIntro({ api, run, error }: StageProps) {
  return (
    <div className="page"><div className="card">
      <h1>练习</h1>
      <p>正式开始前有几道练习题，用来熟悉界面。只有练习题会告诉你参考答案；正式题没有对错反馈。</p>
      <p>用数字键作答：<kbd>1</kbd>左　<kbd>2</kbd>差不多　<kbd>3</kbd>右。<kbd>Backspace</kbd> 撤回上一题，<kbd>Esc</kbd> 暂停。</p>
      <p className="muted">请保持浏览器缩放为 100%，不要拖动或缩放窗口。</p>
      {error && <p className="error">{error}</p>}
      <div className="row"><button className="primary" onClick={() => run(api.startBlock)}>开始练习</button></div>
    </div></div>
  )
}

export function Ready({ state, api, run, error }: StageProps) {
  const done = state.blocks_done ?? 0
  return (
    <div className="page"><div className="card">
      <h1>{done ? `第 ${done} 块已完成` : '准备开始'}</h1>
      <p>每块约 {state.block_size} 题，大约 10–15 分钟。块与块之间可以随时停下，下次用个人链接回来继续。</p>
      {state.completion_code && <p>完成码：<strong>{state.completion_code}</strong></p>}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="primary" onClick={() => run(api.startBlock)}>{done ? '继续下一块' : '开始第 1 块'}</button>
        {done > 0 && <a href="/me"><button>今天到此为止</button></a>}
      </div>
    </div></div>
  )
}

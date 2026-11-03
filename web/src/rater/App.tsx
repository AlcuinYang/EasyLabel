import { useCallback, useEffect, useMemo, useState } from 'react'
import { raterApi } from './api'
import { Consent } from './shell/Consent'
import { Profile } from './shell/Profile'
import { DisplayCheck } from './shell/DisplayCheck'
import { Me } from './shell/Me'
import { Message, PracticeIntro, Ready } from './shell/Pages'
import { TaskFrame } from './TaskFrame'
import type { RaterState } from './types'

export function App() {
  const match = window.location.pathname.match(/^\/x\/([^/]+)/)
  if (!match) return <Me />
  return <Experiment id={decodeURIComponent(match[1])} />
}

function Experiment({ id }: { id: string }) {
  const api = useMemo(() => raterApi(id), [id])
  const [state, setState] = useState<RaterState | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (action: () => Promise<RaterState>) => {
    try {
      setError(null)
      setState(await action())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      try {
        setState(await api.state())
      } catch {
        /* keep the error visible */
      }
    }
  }, [api])

  useEffect(() => {
    void run(api.state)
  }, [api, run])

  if (!state) return <Message title={error ? '无法进入' : '加载中…'} text={error ?? ''} />
  const props = { state, api, run, error }
  switch (state.stage) {
    case 'consent': return <Consent {...props} />
    case 'profile': return <Profile {...props} />
    case 'display': return <DisplayCheck {...props} />
    case 'practice_intro': return <PracticeIntro {...props} />
    case 'ready': return <Ready {...props} />
    case 'task': return <TaskFrame key={state.task!.token} {...props} />
    case 'finished': return <Message title="已完成" text="你已完成本实验目前可分配的全部题目，谢谢参与。" code={state.completion_code} />
    case 'closed': return <Message title="实验已关闭" text="该实验已停止收集数据，谢谢参与。" code={state.completion_code} />
  }
}

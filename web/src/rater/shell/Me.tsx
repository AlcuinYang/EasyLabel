import { useEffect, useState } from 'react'
import { request } from '../../shared/api'

interface MeData {
  anon_id: string
  personal_link: string
  experiments: { id: string; title: string; status: string; completion_code: string | null }[]
}

export function Me() {
  const [data, setData] = useState<MeData | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    request<MeData>('/api/me').then(setData, (e) => setError(e.message))
  }, [])
  if (error) return <div className="page"><div className="card"><h1>未识别身份</h1><p>{error}</p></div></div>
  if (!data) return null
  const link = `${window.location.origin}${data.personal_link}`
  return (
    <div className="page"><div className="card">
      <h1>我的标注（{data.anon_id}）</h1>
      <div className="field">
        <span>个人链接：保存它，换浏览器或下次回来时用</span>
        <input type="text" readOnly value={link} onFocus={(e) => e.currentTarget.select()} />
      </div>
      {data.experiments.map((x) => (
        <div className="row" key={x.id}>
          <span>{x.title}</span>
          {x.status === 'live' ? <a href={`/x/${encodeURIComponent(x.id)}`}><button>进入</button></a>
            : <span className="muted">已关闭</span>}
          {x.completion_code && <span className="muted">完成码 {x.completion_code}</span>}
        </div>
      ))}
    </div></div>
  )
}

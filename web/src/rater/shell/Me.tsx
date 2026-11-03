import { useEffect, useState } from 'react'
import { request } from '../../shared/api'
import { Sheet, Shell } from './Shell'

interface MeData {
  anon_id: string
  personal_link: string
  experiments: { id: string; title: string; status: string; completion_code: string | null }[]
}

export function Me() {
  const [data, setData] = useState<MeData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    request<MeData>('/api/me').then(setData, (e) => setError(e.message))
  }, [])
  if (error) {
    return <Shell><Sheet title="未识别身份" lead={`${error}。请用邀请链接或个人链接进入。`} /></Shell>
  }
  if (!data) return <Shell><Sheet title="加载中…" /></Shell>
  const link = `${window.location.origin}${data.personal_link}`
  const copy = () => void navigator.clipboard?.writeText(link).then(() => setCopied(true), () => undefined)
  return (
    <Shell>
      <Sheet eyebrow={`编号 ${data.anon_id}`} title="我的标注" lead="保存下面的个人链接：换浏览器、换电脑或下次回来时都用它。">
        <div className="actions split" style={{ flexWrap: 'nowrap' }}>
          <input type="text" readOnly value={link} onFocus={(e) => e.currentTarget.select()} />
          <button className="btn" onClick={copy}>{copied ? '已复制' : '复制'}</button>
        </div>
        <div className="exp-list">
          {data.experiments.map((x) => (
            <div className="exp-row" key={x.id}>
              <div>
                <strong>{x.title}</strong>
                <span className="muted">{x.completion_code ? `完成码 ${x.completion_code}` : x.status === 'live' ? '进行中' : '已关闭'}</span>
              </div>
              {x.status === 'live'
                ? <a className="btn primary" href={`/x/${encodeURIComponent(x.id)}`}>进入</a>
                : <span className="muted">已关闭</span>}
            </div>
          ))}
        </div>
      </Sheet>
    </Shell>
  )
}

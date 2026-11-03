import { useState } from 'react'
import type { StageProps } from './types'

const LABELS: Record<string, string> = {
  none: '没有', casual: '偶尔', frequent: '经常', professional: '专业 / 以此为业',
  normal: '正常', deficient: '有色觉异常（色弱 / 色盲）', unsure: '不确定',
}

export function Profile({ state, api, run, error }: StageProps) {
  const options = state.options!
  const [form, setForm] = useState<Record<string, string>>({})
  const fields: [string, string, string[]][] = [
    ['age_band', '年龄段', options.age_band],
    ['photo_experience', '摄影经验', options.experience],
    ['retouch_experience', '修图经验', options.experience],
    ['color_vision', '色觉（自报）', options.color_vision],
  ]
  const complete = fields.every(([key]) => form[key])
  return (
    <div className="page"><div className="card">
      <h1>基本信息</h1>
      <p className="muted">只用于分组统计，不收集姓名或联系方式。</p>
      {fields.map(([key, label, values]) => (
        <label className="field" key={key}>
          <span>{label}</span>
          <select value={form[key] ?? ''} onChange={(e) => setForm({ ...form, [key]: e.target.value })}>
            <option value="" disabled>请选择</option>
            {values.map((v) => <option key={v} value={v}>{LABELS[v] ?? v}</option>)}
          </select>
        </label>
      ))}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="primary" disabled={!complete} onClick={() => run(() => api.profile(form))}>继续</button>
      </div>
    </div></div>
  )
}

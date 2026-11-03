import { useState } from 'react'
import { ErrorNote, Sheet, Shell } from './Shell'
import type { StageProps } from './types'

const LABELS: Record<string, string> = {
  none: '没有', casual: '偶尔', frequent: '经常', professional: '专业 / 以此为业',
  normal: '正常', deficient: '色弱或色盲', unsure: '不确定',
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
    <Shell state={state}>
      <Sheet eyebrow="关于你" title="基本信息" lead="只用于分组统计，不收集姓名或联系方式。">
        {fields.map(([key, label, values]) => (
          <fieldset className="fieldset" key={key}>
            <legend>{label}</legend>
            <div className="options" role="radiogroup" aria-label={label}>
              {values.map((v) => (
                <button key={v} type="button" role="radio" className="opt" aria-checked={form[key] === v}
                  onClick={() => setForm({ ...form, [key]: v })}>{LABELS[v] ?? v}</button>
              ))}
            </div>
          </fieldset>
        ))}
        <ErrorNote error={error} />
        <div className="actions">
          <button className="btn primary lg" disabled={!complete} onClick={() => run(() => api.profile(form))}>继续</button>
        </div>
      </Sheet>
    </Shell>
  )
}

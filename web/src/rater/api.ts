import { post, request } from '../shared/api'
import type { RaterState } from './types'

function sessionId(): string {
  const key = 'el-session'
  let id: string | null = null
  try {
    id = sessionStorage.getItem(key)
    if (!id) {
      id = crypto.randomUUID()
      sessionStorage.setItem(key, id)
    }
  } catch {
    id = id ?? crypto.randomUUID()
  }
  return id
}

const SESSION = sessionId()
const headers = { 'X-Session': SESSION }

export function raterApi(experimentId: string) {
  const base = `/api/x/${encodeURIComponent(experimentId)}`
  return {
    state: () => request<RaterState>(`${base}/state`, { headers }),
    consent: (version: string) => post<RaterState>(`${base}/consent`, { version }, headers),
    profile: (profile: Record<string, string>) => post<RaterState>(`${base}/profile`, profile, headers),
    displayCheck: (payload: Record<string, unknown>) => post<RaterState>(`${base}/display-check`, payload, headers),
    startBlock: () => post<RaterState>(`${base}/block/start`, {}, headers),
    answer: (data: { token: string; payload: unknown; rt_ms: number; env: unknown }) =>
      post<RaterState>(`${base}/answer`, { ...data, client_ts: new Date().toISOString() }, headers),
    undo: () => post<RaterState>(`${base}/undo`, {}, headers),
    event: (type: string, payload: Record<string, unknown> = {}, token?: string) =>
      post<{ ok: boolean }>(`${base}/event`, { type, payload, token }, headers).catch(() => undefined),
  }
}

export type RaterApi = ReturnType<typeof raterApi>

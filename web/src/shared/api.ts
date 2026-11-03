export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init })
  const type = res.headers.get('content-type') || ''
  const body = type.includes('application/json') ? await res.json() : await res.text()
  if (!res.ok) {
    const message = typeof body === 'object' && body ? (body.error ?? body.detail ?? '请求失败') : '请求失败'
    throw new ApiError(String(message), res.status)
  }
  return body as T
}

export function post<T>(path: string, data?: unknown, headers: Record<string, string> = {}): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(data ?? {}),
  })
}

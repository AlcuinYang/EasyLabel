import type { RaterApi } from '../api'
import type { RaterState } from '../types'

export interface StageProps {
  state: RaterState
  api: RaterApi
  run: (action: () => Promise<RaterState>) => Promise<void>
  error: string | null
}

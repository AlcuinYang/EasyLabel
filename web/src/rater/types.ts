export type Scale = '1' | '1.5' | '2'

export interface ImageSize {
  css_width: number
  css_height: number
  src: Record<Scale, string>
}

export interface TaskImage {
  slot: number
  main: ImageSize
  zoom?: ImageSize
}

export interface Task {
  token: string
  role: 'main' | 'practice' | 'followup'
  template: 'pair' | 'grid' | 'region' | 'followup'
  question: string
  images: TaskImage[]
  choices?: string[]
  levels?: number
  max_regions?: number
  region_types?: string[]
  label_map?: string
  low_label?: string
  high_label?: string
  regions?: string[]
}

export interface BlockProgress {
  seq: number
  number: number
  kind: 'practice' | 'main'
  answered: number
  total: number
  can_undo: boolean
}

export interface Feedback {
  correct: boolean
  expected: 'left' | 'right' | 'tie'
}

export interface RaterState {
  stage: 'consent' | 'profile' | 'display' | 'practice_intro' | 'ready' | 'task' | 'finished' | 'closed'
  experiment: { id: string; title: string; template: string; status: string }
  rater: { anon_id: string }
  tier: 'lab' | 'remote'
  completion_code: string | null
  has_practice: boolean
  consent?: { version: string; text: string }
  options?: { age_band: string[]; experience: string[]; color_vision: string[] }
  display?: { allowed_dpr: number[]; pixel_test: boolean }
  display_result?: { passed: boolean; reasons: string[] }
  blocks_done?: number
  block_size?: number
  block?: BlockProgress
  task?: Task
  feedback?: Feedback | null
}

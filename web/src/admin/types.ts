export interface ExperimentRow {
  id: string; title: string; template: string; status: string; config_hash: string | null
  raters: number; answered: number; created_at: string
}

export interface Rate { n: number; success: number; rate: number | null; ci: [number, number] | null }

export interface RaterCard {
  rater: string; arm: string; tier: string; n_main: number
  self_consistency: Rate; swap_consistency: Rate; gold_accuracy: Rate
  rt_median_ms: number | null; rt_fast_share: number | null; left_share: number | null
  excluded: boolean; exclusion_reasons: string[]
  display: { passed: boolean; reasons: string[]; dpr: number; screen: { width: number; height: number } | null;
    pixel_test: string; black: number; white: number } | null
}

export interface Progress {
  target: number; total_items: number; items_at_target: number
  scenes: { scene_id: string; items: number; core: number; counts: number[]; min: number; mean: number; at_target: number }[]
}

export interface Detail {
  id: string; status: string; template: string; config: Record<string, any>; config_hash: string | null
  code_version: string | null; items: Record<string, number>
  invites: { url: string; tier: string; arm: string | null; uses: number; max_uses: number; note: string | null }[]
}

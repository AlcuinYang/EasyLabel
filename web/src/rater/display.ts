// Pure display rules. Images are never scaled by CSS: each is drawn at its CSS size with the file
// whose pixel size equals CSS size × devicePixelRatio. If that is impossible the task is withheld.
import type { Scale, TaskImage } from './types'

export const SCALES: Scale[] = ['1', '1.5', '2']
export const LAYOUT = { side: 24, pairGap: 32, gridGap: 16, header: 92, footer: 104, gridToggle: 40, regionPanel: 360 }

export function pickScale(dpr: number): { scale: Scale; exact: boolean } {
  const exact = SCALES.find((s) => Math.abs(Number(s) - dpr) < 0.01)
  if (exact) return { scale: exact, exact: true }
  const above = SCALES.find((s) => Number(s) >= dpr)
  return { scale: above ?? '2', exact: false }
}

export interface Need {
  width: number
  height: number
}

export function pairNeed(images: TaskImage[]): Need {
  const w = images.reduce((sum, im) => sum + im.main.css_width, 0) + LAYOUT.pairGap * (images.length - 1)
  const h = Math.max(...images.map((im) => im.main.css_height))
  return { width: w + 2 * LAYOUT.side, height: h + LAYOUT.header + LAYOUT.footer }
}

export function gridCell(images: TaskImage[]): Need {
  return {
    width: Math.max(...images.map((im) => im.main.css_width)),
    height: Math.max(...images.map((im) => im.main.css_height)),
  }
}

export function gridNeed(images: TaskImage[], columns = 3): Need {
  const cell = gridCell(images)
  const rows = Math.ceil(images.length / columns)
  return {
    width: columns * cell.width + (columns - 1) * LAYOUT.gridGap + 2 * LAYOUT.side,
    height: rows * (cell.height + LAYOUT.gridToggle) + (rows - 1) * LAYOUT.gridGap + LAYOUT.header + LAYOUT.footer,
  }
}

export function fits(need: Need, win: Need): boolean {
  return win.width >= need.width && win.height >= need.height
}

export interface Env {
  dpr: number
  inner_width: number
  inner_height: number
  visual_viewport_scale: number | null
}

export function currentEnv(): Env {
  return {
    dpr: window.devicePixelRatio,
    inner_width: window.innerWidth,
    inner_height: window.innerHeight,
    visual_viewport_scale: window.visualViewport ? window.visualViewport.scale : null,
  }
}

export function regionNeed(images: TaskImage[]): Need {
  const im = images[0].main
  return {
    width: im.css_width + LAYOUT.pairGap + LAYOUT.regionPanel + 2 * LAYOUT.side,
    height: im.css_height + LAYOUT.header + LAYOUT.footer,
  }
}

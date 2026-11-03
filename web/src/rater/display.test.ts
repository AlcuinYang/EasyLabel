import { describe, expect, it } from 'vitest'
import { fits, gridNeed, pairNeed, pickScale } from './display'
import type { TaskImage } from './types'

const img = (w: number, h: number): TaskImage => ({
  slot: 0,
  main: { css_width: w, css_height: h, src: { '1': '', '1.5': '', '2': '' } },
})

describe('pickScale', () => {
  it('matches exact device pixel ratios', () => {
    expect(pickScale(1)).toEqual({ scale: '1', exact: true })
    expect(pickScale(1.5)).toEqual({ scale: '1.5', exact: true })
    expect(pickScale(2)).toEqual({ scale: '2', exact: true })
  })
  it('flags non-exact ratios (browser zoom, 125% scaling)', () => {
    expect(pickScale(1.25)).toEqual({ scale: '1.5', exact: false })
    expect(pickScale(3)).toEqual({ scale: '2', exact: false })
  })
})

describe('window fit', () => {
  it('a 1024 landscape pair does not fit a 1920 CSS-px window (4K at 200%)', () => {
    const need = pairNeed([img(1024, 682), img(1024, 682)])
    expect(need.width).toBe(1024 * 2 + 32 + 48)
    expect(fits(need, { width: 1920, height: 1080 })).toBe(false)
    expect(fits(need, { width: 2560, height: 1440 })).toBe(true)
  })
  it('grid uses the largest cell for every slot', () => {
    const need = gridNeed([img(480, 320), img(480, 320), img(480, 320), img(320, 480), img(480, 320), img(480, 320)])
    expect(need.width).toBe(3 * 480 + 2 * 16 + 48)
    expect(need.height).toBe(2 * (480 + 40) + 16 + 92 + 104)
  })
})

import { describe, expect, it } from 'vitest'
import {
  gateConclusion,
  gateOneLiner,
  buildWhyBullets,
  trendLabel,
  sentimentLabel,
  dataQualityLabel,
  formatCapPct,
  metricLabel,
} from './regimeCopy'

describe('regimeCopy', () => {
  it('maps gate conclusions', () => {
    expect(gateConclusion('risk_off')).toBe('今天先别急着买')
    expect(gateConclusion('defensive')).toBe('先轻仓观望')
    expect(gateConclusion('normal')).toBe('正常参与即可')
    expect(gateConclusion('aggressive')).toBe('可以积极一点')
  })

  it('formats position cap', () => {
    expect(formatCapPct(0.35)).toBe('35%')
  })

  it('builds at most 3 why bullets from notes', () => {
    const bullets = buildWhyBullets({
      gate_level: 'defensive',
      trend_regime: 'range',
      data_quality: 'ok',
      evidence: [
        { key: 'a', value: '1', note: '涨停变少，赚钱效应转弱' },
        { key: 'b', value: '2', note: '连板高度回落，接力变难' },
        { key: 'c', value: '3', note: '指数走震荡，不宜重仓' },
        { key: 'd', value: '4', note: '多余第四条不应出现' },
      ],
    })
    expect(bullets).toHaveLength(3)
    expect(bullets[0]).toContain('涨停变少')
  })

  it('includes data-quality bullet when degraded', () => {
    const bullets = buildWhyBullets({
      gate_level: 'defensive',
      data_quality: 'degraded',
      evidence: [],
      metrics: {},
    })
    expect(bullets.some((b) => /缺失|降级|保守/.test(b))).toBe(true)
    expect(bullets.length).toBeGreaterThanOrEqual(1)
    expect(bullets.length).toBeLessThanOrEqual(3)
  })

  it('maps trend/sentiment/quality and metric keys', () => {
    expect(trendLabel('range')).toBe('震荡')
    expect(sentimentLabel('ebb')).toBe('退潮')
    expect(dataQualityLabel('ok')).toBe('可用')
    expect(metricLabel('seal_rate')).toBe('封板率')
  })
})

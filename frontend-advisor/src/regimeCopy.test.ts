import { describe, expect, it } from 'vitest'
import {
  gateConclusion,
  gateShortLabel,
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

  it('maps compact gate labels', () => {
    expect(gateShortLabel('risk_off')).toBe('风险关闭')
    expect(gateShortLabel('defensive')).toBe('轻仓观望')
    expect(gateShortLabel('normal')).toBe('正常')
    expect(gateShortLabel('aggressive')).toBe('可积极')
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

  it('builds human why bullets when backend evidence starts with trend feature notes', () => {
    const bullets = buildWhyBullets({
      gate_level: 'defensive',
      trend_regime: 'range',
      data_quality: 'ok',
      evidence: [
        { key: 'ma_stack', value: 'mixed', note: '指数相对 MA 排列' },
        { key: 'drawdown_from_high', value: 0.085, note: '距阶段高点回撤' },
        { key: 'breadth', value: 0.42, note: '上涨家数占比' },
        { key: 'volume_vs_ma20', value: 0.91, note: '成交额相对 20 日均' },
        { key: 'trend_regime', value: 'range', note: '未满足明确上升或下降趋势条件' },
        { key: 'promotion_rate', value: null, note: '缺少昨日连板归档，晋级率不可用' },
        { key: 'limit_up_count', value: 18, note: '涨停家数' },
        { key: 'seal_rate', value: 0.36, note: '封板率' },
        { key: 'max_board', value: 3, note: '最高连板' },
        { key: 'sentiment_cycle', value: 'ebb', note: '前一归档为高潮，当前温度回落' },
      ],
      metrics: {
        seal_rate: 0.36,
        promotion_rate: null,
        max_board: 3,
      },
    })

    expect(bullets).toEqual([
      '前一归档为高潮，当前温度回落',
      '连板高度或晋级回落，接力变难',
      '涨停变少，赚钱效应转弱',
    ])
    expect(bullets.join(' ')).not.toMatch(
      /指数相对 MA 排列|距阶段高点回撤|上涨家数占比|成交额相对 20 日均|封板率|涨停家数|最高连板/,
    )
  })

  it('maps trend/sentiment/quality and metric keys', () => {
    expect(trendLabel('range')).toBe('震荡')
    expect(sentimentLabel('ebb')).toBe('退潮')
    expect(dataQualityLabel('ok')).toBe('可用')
    expect(metricLabel('seal_rate')).toBe('封板率')
    expect(metricLabel('ma_stack')).toBe('均线排列')
    expect(metricLabel('drawdown_from_high')).toBe('高点回撤')
    expect(metricLabel('breadth')).toBe('市场宽度')
    expect(metricLabel('volume_vs_ma20')).toBe('成交额/20日均')
    expect(metricLabel('data_quality')).toBe('数据质量')
    expect(metricLabel('promotion_rate')).toBe('晋级率')
    expect(metricLabel('promotion_k3')).toBe('3连晋级率')
    expect(metricLabel('limit_up_count')).toBe('涨停家数')
    expect(metricLabel('broken_count')).toBe('炸板家数')
    expect(metricLabel('max_board')).toBe('最高连板')
    expect(metricLabel('height_board_count')).toBe('高度板家数')
    expect(metricLabel('break_rate')).toBe('炸板率')
    expect(metricLabel('sentiment_score')).toBe('情绪得分')
  })
})

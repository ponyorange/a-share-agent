const GATE_CONCLUSIONS: Record<string, string> = {
  aggressive: '可以积极一点',
  normal: '正常参与即可',
  defensive: '先轻仓观望',
  risk_off: '今天先别急着买',
}

const GATE_ONE_LINERS: Record<string, string> = {
  aggressive: '趋势与情绪都偏强，可按上限内正常参与，仍控制单票风险。',
  normal: '市场中性偏稳，按计划交易即可，不必追也不必空仓。',
  defensive: '赚钱效应转弱或结构一般，先降仓位、少开新仓。',
  risk_off: '环境偏危险，默认不推新买入；守住现金与持仓风控更重要。',
}

const GATE_SHORT_LABELS: Record<string, string> = {
  aggressive: '可积极',
  normal: '正常',
  defensive: '轻仓观望',
  risk_off: '风险关闭',
}

const TREND_LABELS: Record<string, string> = {
  uptrend: '上行',
  range: '震荡',
  downtrend: '下行',
}

const SENTIMENT_LABELS: Record<string, string> = {
  ice: '冰点',
  repair: '修复',
  strengthen: '增强',
  climax: '高潮',
  ebb: '退潮',
}

const DATA_QUALITY_LABELS: Record<string, string> = {
  ok: '可用',
  degraded: '部分缺失（已偏保守）',
  failed: '暂不可靠（请降仓）',
}

const METRIC_LABELS: Record<string, string> = {
  ma_stack: '均线排列',
  drawdown_from_high: '高点回撤',
  breadth: '市场宽度',
  volume_vs_ma20: '成交额/20日均',
  data_quality: '数据质量',
  seal_rate: '封板率',
  break_rate: '炸板率',
  limit_up_count: '涨停家数',
  limit_down_count: '跌停家数',
  broken_count: '炸板家数',
  max_board: '最高连板',
  height_board_count: '高度板家数',
  promotion_rate: '晋级率',
  sentiment_score: '情绪得分',
  sentiment_cycle: '情绪周期',
  trend_regime: '趋势状态',
  gate_level: '闸门等级',
  position_cap: '仓位上限',
}

const KEY_HEURISTIC_BULLETS: Record<string, string> = {
  seal_rate: '涨停变少，赚钱效应转弱',
  limit_up_count: '涨停变少，赚钱效应转弱',
  promotion: '连板高度或晋级回落，接力变难',
  promotion_rate: '连板高度或晋级回落，接力变难',
  max_board: '连板高度或晋级回落，接力变难',
  trend: '指数偏弱或震荡，不宜重仓',
  trend_regime: '指数偏弱或震荡，不宜重仓',
}

const RAW_METRIC_NOTE_KEYS = new Set([
  'ma_stack',
  'drawdown_from_high',
  'breadth',
  'volume_vs_ma20',
  'limit_up_count',
  'limit_down_count',
  'broken_count',
  'seal_rate',
  'break_rate',
  'max_board',
  'height_board_count',
  'promotion_rate',
  'sentiment_score',
])

const RAW_METRIC_NOTES = new Set([
  '指数相对 MA 排列',
  '距阶段高点回撤',
  '上涨家数占比',
  '成交额相对 20 日均',
  '涨停家数',
  '跌停家数',
  '炸板家数',
  '封板率',
  '炸板率',
  '最高连板',
  '高度板家数',
  '情绪温度分',
])

const MAX_BULLETS = 3
const MAX_NOTE_CHARS = 28

export type BuildWhyBulletsInput = {
  gate_level?: string
  trend_regime?: string
  data_quality?: string
  evidence?: { key: string; value: string | number | null; note?: string | null }[]
  metrics?: Record<string, unknown>
}

function mapLabel(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—'
  return map[value] ?? value
}

function truncateNote(note: string): string {
  const trimmed = note.trim()
  if (trimmed.length <= MAX_NOTE_CHARS) return trimmed
  return trimmed.slice(0, MAX_NOTE_CHARS)
}

function dataQualityWhyBullet(quality: string): string {
  if (quality === 'degraded') return '部分数据缺失，判断已偏保守'
  if (quality === 'failed') return '数据暂不可靠，请降仓并保守操作'
  return '数据质量异常，判断已偏保守'
}

function heuristicForKey(key: string): string | undefined {
  return KEY_HEURISTIC_BULLETS[key]
}

function isRawMetricNote(key: string, note: string): boolean {
  if (RAW_METRIC_NOTES.has(note)) return true
  if (/^缺少.+不可用$/.test(note)) return true
  if (/^\d+连\s+\d+\s+\/\s+昨\d+连\s+\d+$/.test(note)) return true
  if (/^2连板\s+\d+\s+\/\s+昨首板\s+\d+$/.test(note)) return true
  return RAW_METRIC_NOTE_KEYS.has(key) && /^(缺少|无)?[\w\u4e00-\u9fa5\s/MA0-9（）()-]+(占比|排列|回撤|均|家数|连板|分|率|样本|不可用|记为 0)$/.test(note)
}

function isActionableNote(key: string, note: string): boolean {
  if (!note || isRawMetricNote(key, note)) return false
  if (key === 'trend_regime') return false
  if (key === 'data_quality') return false
  if (key === 'sentiment_cycle' || key === 'gate_level') return true
  return /弱|少|难|回落|转弱|退潮|冰点|修复|增强|高潮|危险|保守|观望|不宜|降仓|偏弱|偏强|满足|未满足/.test(
    note,
  )
}

function pushUnique(bullets: string[], line: string) {
  if (bullets.length >= MAX_BULLETS) return
  if (!line || bullets.includes(line)) return
  bullets.push(line)
}

export function gateConclusion(level: string | null | undefined): string {
  return mapLabel(GATE_CONCLUSIONS, level)
}

export function gateOneLiner(level: string | null | undefined): string {
  return mapLabel(GATE_ONE_LINERS, level)
}

export function gateShortLabel(level: string | null | undefined): string {
  return mapLabel(GATE_SHORT_LABELS, level)
}

export function trendLabel(v: string | null | undefined): string {
  return mapLabel(TREND_LABELS, v)
}

export function sentimentLabel(v: string | null | undefined): string {
  return mapLabel(SENTIMENT_LABELS, v)
}

export function dataQualityLabel(v: string | null | undefined): string {
  return mapLabel(DATA_QUALITY_LABELS, v)
}

export function formatCapPct(cap: number | null | undefined): string {
  if (cap == null || Number.isNaN(cap)) return '—'
  return `${Math.round(cap * 100)}%`
}

export function metricLabel(key: string): string {
  const promotionK = key.match(/^promotion_k(\d+)$/)
  if (promotionK) return `${promotionK[1]}连晋级率`
  return METRIC_LABELS[key] ?? key
}

export function buildWhyBullets(input: BuildWhyBulletsInput): string[] {
  const bullets: string[] = []
  const quality = input.data_quality ?? 'ok'
  const evidence = input.evidence ?? []

  for (const item of evidence) {
    const note = item.note?.trim()
    if (note && isActionableNote(item.key, note)) {
      pushUnique(bullets, truncateNote(note))
    }
  }

  if (bullets.length < MAX_BULLETS) {
    for (const item of evidence) {
      if (item.key === 'trend' || item.key === 'trend_regime') continue
      const line = heuristicForKey(item.key)
      if (line) pushUnique(bullets, line)
    }
  }

  if (bullets.length < MAX_BULLETS && input.metrics) {
    for (const key of Object.keys(input.metrics)) {
      const line = heuristicForKey(key)
      if (line) pushUnique(bullets, line)
    }
  }

  if (bullets.length < MAX_BULLETS && input.trend_regime) {
    const trend = input.trend_regime
    if (trend === 'range' || trend === 'downtrend') {
      pushUnique(bullets, KEY_HEURISTIC_BULLETS.trend_regime)
    }
  }

  const gateLevel = input.gate_level
  if (bullets.length < MAX_BULLETS && gateLevel) {
    const fallback = GATE_ONE_LINERS[gateLevel]
    if (fallback) pushUnique(bullets, fallback)
  }

  if (quality !== 'ok') {
    const qualityLine = dataQualityWhyBullet(quality)
    const hasQuality = bullets.some((b) => /缺失|降级|保守|不可靠/.test(b))
    if (!hasQuality) {
      if (bullets.length >= MAX_BULLETS) {
        bullets[MAX_BULLETS - 1] = qualityLine
      } else {
        pushUnique(bullets, qualityLine)
      }
    }
  }

  while (bullets.length < MAX_BULLETS && gateLevel) {
    const fallback = GATE_ONE_LINERS[gateLevel]
    if (!fallback || bullets.includes(fallback)) break
    pushUnique(bullets, fallback)
  }

  if (bullets.length === 0 && gateLevel) {
    const fallback = GATE_ONE_LINERS[gateLevel]
    if (fallback) bullets.push(fallback)
  }

  return bullets.slice(0, MAX_BULLETS)
}

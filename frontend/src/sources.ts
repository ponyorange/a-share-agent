export type SourceFeature =
  | 'explorer'
  | 'market'
  | 'kline'
  | 'quote'
  | 'fund'
  | 'limitup'

export type DataSourceInfo = {
  id: string
  label: string
  features: SourceFeature[]
  docs_url?: string
  ready?: boolean
  message?: string | null
}

/** Fallback when /api/sources is unavailable */
export const FALLBACK_SOURCES: DataSourceInfo[] = [
  {
    id: 'akshare',
    label: 'AKShare',
    features: ['explorer', 'market', 'kline', 'quote', 'fund', 'limitup'],
    docs_url: 'https://akshare.akfamily.xyz/',
    ready: true,
  },
  {
    id: 'tushare',
    label: 'Tushare',
    features: ['explorer'],
    docs_url: 'https://tushare.pro/document/2',
    ready: false,
    message: '需配置 TUSHARE_TOKEN',
  },
  {
    id: 'baostock',
    label: 'BaoStock',
    features: ['explorer', 'kline'],
    docs_url: 'http://baostock.com/baostock/index.php',
    ready: true,
  },
]

export const DEFAULT_SOURCE = 'akshare'

export function hasFeature(
  source: DataSourceInfo | undefined,
  feature: SourceFeature,
): boolean {
  return Boolean(source?.features.includes(feature))
}

export function sourcePath(sourceId: string, feature: SourceFeature = 'explorer'): string {
  if (feature === 'explorer') return `/${sourceId}`
  if (feature === 'market') return `/${sourceId}/market`
  if (feature === 'kline') return `/${sourceId}/kline`
  if (feature === 'fund') return `/${sourceId}/fund`
  if (feature === 'limitup') return `/${sourceId}/limitup`
  return `/${sourceId}`
}

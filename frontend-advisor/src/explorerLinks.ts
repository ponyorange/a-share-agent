/** 数据后台（explorer）K 线链接：生产同源 /explorer，本地开发走 5173。 */
export function explorerKlineUrl(symbol: string, range = 'daily'): string {
  const qs = new URLSearchParams({
    symbol: symbol.trim(),
    range,
  })
  if (import.meta.env.PROD) {
    return `/explorer/akshare/kline?${qs}`
  }
  return `http://127.0.0.1:5173/akshare/kline?${qs}`
}

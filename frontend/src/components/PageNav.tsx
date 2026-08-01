import { Link, useParams } from 'react-router-dom'
import {
  DEFAULT_SOURCE,
  hasFeature,
  sourcePath,
  type DataSourceInfo,
  type SourceFeature,
} from '../sources'

type Props = {
  sources: DataSourceInfo[]
  activeFeature: SourceFeature
}

export function PageNav({ sources, activeFeature }: Props) {
  const params = useParams()
  const sourceId = (params.source || DEFAULT_SOURCE).toLowerCase()
  const current = sources.find((s) => s.id === sourceId) ?? sources[0]

  return (
    <div className="nav-cluster">
      <div className="source-switch" role="tablist" aria-label="数据源">
        {sources.map((s) => (
          <Link
            key={s.id}
            to={sourcePath(s.id, hasFeature(s, activeFeature) ? activeFeature : 'explorer')}
            role="tab"
            aria-selected={s.id === sourceId}
            className={s.id === sourceId ? 'active' : ''}
            title={s.message || s.label}
          >
            {s.label}
          </Link>
        ))}
      </div>
      <nav className="page-nav" aria-label="功能">
        <Link
          to={sourcePath(sourceId, 'explorer')}
          className={activeFeature === 'explorer' ? 'active' : ''}
        >
          接口浏览器
        </Link>
        {hasFeature(current, 'market') ? (
          <Link
            to={sourcePath(sourceId, 'market')}
            className={activeFeature === 'market' ? 'active' : ''}
          >
            大盘行情
          </Link>
        ) : null}
        {hasFeature(current, 'kline') ? (
          <Link
            to={sourcePath(sourceId, 'kline')}
            className={activeFeature === 'kline' ? 'active' : ''}
          >
            K线图
          </Link>
        ) : null}
        {hasFeature(current, 'limitup') ? (
          <Link
            to={sourcePath(sourceId, 'limitup')}
            className={activeFeature === 'limitup' ? 'active' : ''}
          >
            打板
          </Link>
        ) : null}
        {hasFeature(current, 'fund') ? (
          <Link
            to={sourcePath(sourceId, 'fund')}
            className={activeFeature === 'fund' ? 'active' : ''}
          >
            基金详情
          </Link>
        ) : null}
      </nav>
    </div>
  )
}

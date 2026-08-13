import { useEffect, useMemo, useRef, useState } from 'react'
import { DirectedGraph } from 'graphology'
import Sigma from 'sigma'
import type { GraphViewEdge, GraphViewPayload } from '../api'
import {
  LAYER_LABELS,
  edgeKey,
  filterVisibleGraph,
  formatGraphNodeId,
  layerCoordinates,
  shouldShowLabel,
  type HighlightState,
} from '../signalGraphLayout'

function actionColor(dst: string, alpha = 1): string {
  const hex = dst.endsWith('BUY') ? '#3d9a6a' : dst.endsWith('SELL') ? '#c45c5c' : '#c4a35a'
  if (alpha >= 1) return hex
  const n = hex.replace('#', '')
  const r = Number.parseInt(n.slice(0, 2), 16)
  const g = Number.parseInt(n.slice(2, 4), 16)
  const b = Number.parseInt(n.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function edgeSize(edge: GraphViewEdge): number {
  const mag = Math.abs(edge.confidence) + Math.log1p(edge.sample_count)
  return Math.min(5, 0.8 + mag * 0.4)
}

function nodeSize(layer: string): number {
  if (layer === 'action') return 14
  if (layer === 'stock') return 4
  return 8
}

function extraStockKey(highlight: HighlightState | null, showStocks: boolean): string {
  if (showStocks || !highlight) return ''
  return [...highlight.nodeIds].filter((id) => id.startsWith('stock:')).sort().join(',')
}

type Props = {
  payload: GraphViewPayload | null
  error: string | null
  highlight: HighlightState | null
  onSelectNode: (id: string) => void
  onResetView: () => void
}

export function SignalGraphView({
  payload,
  error,
  highlight,
  onSelectNode,
  onResetView,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const hoveredRef = useRef<string | null>(null)
  const highlightRef = useRef(highlight)
  highlightRef.current = highlight
  const onSelectRef = useRef(onSelectNode)
  onSelectRef.current = onSelectNode
  const [showStocks, setShowStocks] = useState(false)
  const [showColdStart, setShowColdStart] = useState(false)

  const extraKey = extraStockKey(highlight, showStocks)
  const extraNodeIds = useMemo(() => {
    if (!extraKey) return undefined
    return new Set(extraKey.split(',').filter(Boolean))
  }, [extraKey])

  const filtered = useMemo(() => {
    if (!payload) return null
    return filterVisibleGraph(payload, { showStocks, showColdStart, extraNodeIds })
  }, [payload, showStocks, showColdStart, extraNodeIds])

  useEffect(() => {
    const el = hostRef.current
    if (!el || !filtered || filtered.edges.length === 0) return

    const graph = new DirectedGraph()
    const pos = layerCoordinates(filtered.nodes)
    for (const node of filtered.nodes) {
      const p = pos[node.id] || { x: 2, y: 0.5 }
      graph.addNode(node.id, {
        x: p.x,
        y: p.y,
        label: node.label,
        layer: node.layer,
        size: nodeSize(node.layer),
        color: node.layer === 'action' ? actionColor(`action:${node.label}`) : '#5b6475',
      })
    }
    for (const edge of filtered.edges) {
      if (!graph.hasNode(edge.src) || !graph.hasNode(edge.dst)) continue
      if (graph.hasEdge(edge.src, edge.dst)) continue
      graph.addEdge(edge.src, edge.dst, {
        size: edgeSize(edge),
        color: actionColor(edge.dst, edge.sample_count === 0 ? 0.18 : 1),
        sample_count: edge.sample_count,
        confidence: edge.confidence,
        key: edgeKey(edge.src, edge.dst),
      })
    }

    let instance: Sigma | null = null
    const cameraRatio = () => instance?.getCamera().ratio ?? 1
    try {
      instance = new Sigma(graph, el, {
        renderEdgeLabels: false,
        labelDensity: 0.07,
        labelRenderedSizeThreshold: 0,
        nodeReducer(node, data) {
          const h = highlightRef.current
          const highlighted = h?.nodeIds ?? new Set<string>()
          const show = shouldShowLabel({
            id: node,
            layer: String(data.layer || ''),
            ratio: cameraRatio(),
            hoveredId: hoveredRef.current,
            highlighted,
          })
          const dim = Boolean(h && h.nodeIds.size && !h.nodeIds.has(node))
          return {
            ...data,
            label: show ? data.label : null,
            forceLabel: show && (highlighted.has(node) || String(data.layer) !== 'stock'),
            color: dim ? '#c5cad3' : data.color,
            zIndex: highlighted.has(node) ? 2 : 0,
          }
        },
        edgeReducer(edge, data) {
          const h = highlightRef.current
          const src = graph.source(edge)
          const dst = graph.target(edge)
          const key = edgeKey(src, dst)
          const dim = Boolean(h && h.edgeKeys.size && !h.edgeKeys.has(key))
          return {
            ...data,
            hidden: false,
            color: dim ? 'rgba(180,186,196,0.12)' : data.color,
            zIndex: h?.edgeKeys.has(key) ? 2 : 0,
          }
        },
      })
    } catch (err) {
      sigmaRef.current = null
      el.replaceChildren()
      el.textContent = err instanceof Error ? err.message : String(err)
      return
    }
    instance.on('clickNode', ({ node }) => {
      if (String(node).startsWith('action:')) return
      onSelectRef.current(String(node))
    })
    instance.on('enterNode', ({ node }) => {
      hoveredRef.current = String(node)
      instance?.refresh()
    })
    instance.on('leaveNode', () => {
      hoveredRef.current = null
      instance?.refresh()
    })
    sigmaRef.current = instance
    return () => {
      instance?.kill()
      sigmaRef.current = null
    }
  }, [filtered])

  useEffect(() => {
    sigmaRef.current?.refresh()
  }, [highlight])

  if (error) {
    return <p className="status error">{error}</p>
  }
  if (!payload || payload.edges.length === 0) {
    return <p className="status">图还是空的，先生成或等自进化写入边</p>
  }

  const highlightedEdges = highlight
    ? payload.edges.filter((e) => highlight.edgeKeys.has(edgeKey(e.src, e.dst)))
    : []
  const layers = filtered?.layers ?? []
  const hiddenNote =
    filtered && (filtered.hiddenStocks || filtered.hiddenColdStart)
      ? `已隐藏 ${filtered.hiddenStocks} 个股${
          filtered.hiddenColdStart ? `、${filtered.hiddenColdStart} 条未学习边` : ''
        }`
      : ''

  return (
    <div className="signal-graph-view">
      <div className="signal-graph-toolbar">
        <button type="button" className="btn ghost" onClick={() => {
          onResetView()
          void sigmaRef.current?.getCamera().animatedReset()
        }}>
          返回全图
        </button>
        <label>
          <input
            type="checkbox"
            checked={showStocks}
            onChange={(e) => setShowStocks(e.target.checked)}
          />
          个股
        </label>
        <label>
          <input
            type="checkbox"
            checked={showColdStart}
            onChange={(e) => setShowColdStart(e.target.checked)}
          />
          冷启动边
        </label>
        <div className="signal-graph-legend" aria-hidden="true">
          <span><i style={{ background: '#3d9a6a' }} />买入</span>
          <span><i style={{ background: '#c4a35a' }} />持有</span>
          <span><i style={{ background: '#c45c5c' }} />卖出</span>
        </div>
        {payload.truncated ? (
          <span className="muted">节点/边达上限，已按强度截断</span>
        ) : (
          <span className="muted">
            {filtered ? `显示 ${filtered.nodes.length} 点 · ${filtered.edges.length} 边` : ''}
            {hiddenNote ? ` · ${hiddenNote}` : ''}
          </span>
        )}
      </div>
      <div
        className="signal-graph-stage"
        style={{ ['--cols' as string]: String(Math.max(layers.length, 1)) }}
      >
        <div className="signal-graph-cols">
          {layers.map((layer) => (
            <span key={layer}>{LAYER_LABELS[layer] || layer}</span>
          ))}
        </div>
        {filtered && filtered.edges.length === 0 ? (
          <p className="status">当前过滤下没有边，打开「个股」或「冷启动边」看看全图</p>
        ) : (
          <div ref={hostRef} className="signal-graph-canvas" aria-label="图学习分层图" />
        )}
      </div>
      {highlightedEdges.length ? (
        <div className="signal-graph-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>路径</th>
                <th>信心</th>
                <th>样本</th>
              </tr>
            </thead>
            <tbody>
              {highlightedEdges.map((edge) => (
                <tr key={edgeKey(edge.src, edge.dst)}>
                  <td>
                    {formatGraphNodeId(edge.src)} → {formatGraphNodeId(edge.dst)}
                  </td>
                  <td>{edge.confidence.toFixed(2)}</td>
                  <td>{edge.sample_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

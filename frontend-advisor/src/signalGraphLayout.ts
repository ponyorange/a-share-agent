import type {
  GraphSignalEvidence,
  GraphViewEdge,
  GraphViewNode,
  GraphViewPayload,
} from './api'

export const LAYER_ORDER = ['market', 'industry', 'pattern', 'stock', 'action'] as const

export const LAYER_LABELS: Record<string, string> = {
  market: '市场',
  industry: '行业',
  pattern: '形态',
  stock: '个股',
  action: '建议',
}

/** Fallback x when a layer is present; compact layout remaps to 0..n-1. */
export const LAYER_X: Record<string, number> = {
  market: 0,
  industry: 1,
  pattern: 2,
  stock: 3,
  action: 4,
}

export const ACTION_ORDER = ['BUY', 'HOLD', 'SELL'] as const

export const ACTION_LABELS: Record<string, string> = {
  BUY: '买入',
  HOLD: '持有',
  SELL: '卖出',
}

export const LABEL_ZOOM_RATIO = 0.35

export type VisibleGraphFilter = {
  showStocks: boolean
  showColdStart: boolean
  extraNodeIds?: Set<string>
}

export type VisibleGraph = {
  nodes: GraphViewNode[]
  edges: GraphViewEdge[]
  hiddenStocks: number
  hiddenColdStart: number
  layers: string[]
}

export type HighlightState = {
  nodeIds: Set<string>
  edgeKeys: Set<string>
}

export function edgeKey(src: string, dst: string): string {
  return `${src}->${dst}`
}

export function formatGraphAction(action: string | undefined): string {
  if (!action) return '—'
  return ACTION_LABELS[action] || action
}

export function formatGraphNodeId(id: string): string {
  const sep = id.indexOf(':')
  if (sep < 0) return id
  const layer = id.slice(0, sep)
  const name = id.slice(sep + 1)
  if (layer === 'action') return formatGraphAction(name)
  if (layer === 'stock') return name
  const prefix = LAYER_LABELS[layer]
  return prefix ? `${prefix} ${name}` : id
}

export function visibleLayers(nodes: GraphViewNode[]): string[] {
  const present = new Set(nodes.map((node) => node.layer || 'pattern'))
  const known = LAYER_ORDER.filter((layer) => present.has(layer))
  const extra = [...present].filter((layer) => !LAYER_ORDER.includes(layer as (typeof LAYER_ORDER)[number]))
  extra.sort()
  return [...known, ...extra]
}

export function filterVisibleGraph(
  payload: GraphViewPayload,
  opts: VisibleGraphFilter,
): VisibleGraph {
  const extra = opts.extraNodeIds ?? new Set<string>()
  const nodes = payload.nodes.filter((node) => {
    if (node.layer !== 'stock') return true
    return opts.showStocks || extra.has(node.id)
  })
  const hiddenStocks = payload.nodes.filter((node) => node.layer === 'stock').length
    - nodes.filter((node) => node.layer === 'stock').length
  const visibleIds = new Set(nodes.map((node) => node.id))
  let hiddenColdStart = 0
  const edges = payload.edges.filter((edge) => {
    if (!visibleIds.has(edge.src) || !visibleIds.has(edge.dst)) return false
    if (!opts.showColdStart && edge.sample_count === 0) {
      hiddenColdStart += 1
      return false
    }
    return true
  })
  return {
    nodes,
    edges,
    hiddenStocks,
    hiddenColdStart,
    layers: visibleLayers(nodes),
  }
}

export function layerCoordinates(
  nodes: GraphViewNode[],
): Record<string, { x: number; y: number }> {
  const groups = new Map<string, GraphViewNode[]>()
  for (const node of nodes) {
    const layer = node.layer || 'pattern'
    const list = groups.get(layer) || []
    list.push(node)
    groups.set(layer, list)
  }
  const layers = visibleLayers(nodes)
  const xOf = new Map(layers.map((layer, i) => [layer, i]))
  const out: Record<string, { x: number; y: number }> = {}
  for (const [layer, list] of groups) {
    const x = xOf.get(layer) ?? LAYER_X[layer] ?? 2
    const sorted = [...list]
    if (layer === 'action') {
      sorted.sort((a, b) => {
        const ia = ACTION_ORDER.indexOf(a.label as (typeof ACTION_ORDER)[number])
        const ib = ACTION_ORDER.indexOf(b.label as (typeof ACTION_ORDER)[number])
        const sa = ia === -1 ? 99 : ia
        const sb = ib === -1 ? 99 : ib
        if (sa !== sb) return sa - sb
        return a.label.localeCompare(b.label)
      })
    } else {
      sorted.sort((a, b) => a.label.localeCompare(b.label))
    }
    const n = sorted.length
    sorted.forEach((node, i) => {
      const y = n === 1 ? 0.5 : i / (n - 1)
      out[node.id] = { x, y }
    })
  }
  return out
}

export function highlightFromNode(
  nodeId: string,
  edges: GraphViewEdge[],
): HighlightState {
  const nodeIds = new Set<string>([nodeId])
  const edgeKeys = new Set<string>()
  for (const edge of edges) {
    if (edge.src === nodeId && edge.dst.startsWith('action:')) {
      edgeKeys.add(edgeKey(edge.src, edge.dst))
      nodeIds.add(edge.dst)
    }
  }
  return { nodeIds, edgeKeys }
}

export function highlightFromEvidence(
  evidence: GraphSignalEvidence[],
  action?: string,
): HighlightState {
  const nodeIds = new Set<string>()
  const edgeKeys = new Set<string>()
  for (const item of evidence) {
    const src = item.src
    if (!src) continue
    const dst = item.dst || (action ? `action:${action}` : '')
    nodeIds.add(src)
    if (dst) {
      nodeIds.add(dst)
      edgeKeys.add(edgeKey(src, dst))
    }
  }
  if (action) nodeIds.add(`action:${action}`)
  return { nodeIds, edgeKeys }
}

export function shouldShowLabel(args: {
  id: string
  layer: string
  ratio: number
  hoveredId?: string | null
  highlighted: Set<string>
}): boolean {
  if (args.layer === 'action' || args.layer === 'market' || args.layer === 'industry' || args.layer === 'pattern') {
    return true
  }
  if (args.id === args.hoveredId) return true
  if (args.highlighted.has(args.id)) return true
  if (args.ratio < LABEL_ZOOM_RATIO) return true
  return false
}

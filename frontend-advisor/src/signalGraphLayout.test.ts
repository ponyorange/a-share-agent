import { expect, it } from 'vitest'
import {
  edgeKey,
  filterVisibleGraph,
  formatGraphAction,
  formatGraphNodeId,
  highlightFromEvidence,
  highlightFromNode,
  layerCoordinates,
  shouldShowLabel,
} from './signalGraphLayout'

it('assigns x by layer and stacks y', () => {
  const pos = layerCoordinates([
    { id: 'industry:b', layer: 'industry', label: 'b' },
    { id: 'industry:a', layer: 'industry', label: 'a' },
    { id: 'action:BUY', layer: 'action', label: 'BUY' },
    { id: 'action:SELL', layer: 'action', label: 'SELL' },
    { id: 'action:HOLD', layer: 'action', label: 'HOLD' },
  ])
  expect(pos['industry:a'].x).toBeLessThan(pos['action:BUY'].x)
  expect(pos['industry:a'].x).toBe(0)
  expect(pos['action:BUY'].x).toBe(1)
  expect(pos['industry:a'].y).toBeLessThan(pos['industry:b'].y)
  expect(pos['action:BUY'].y).toBeLessThan(pos['action:HOLD'].y)
  expect(pos['action:HOLD'].y).toBeLessThan(pos['action:SELL'].y)
})

it('highlights a context node neighborhood', () => {
  const h = highlightFromNode('industry:food', [
    {
      src: 'industry:food',
      dst: 'action:BUY',
      layer: 'industry',
      confidence: 1,
      sample_count: 2,
      last_tick: 1,
    },
    {
      src: 'stock:x',
      dst: 'action:SELL',
      layer: 'stock',
      confidence: 1,
      sample_count: 2,
      last_tick: 1,
    },
  ])
  expect(h.nodeIds.has('industry:food')).toBe(true)
  expect(h.nodeIds.has('action:BUY')).toBe(true)
  expect(h.edgeKeys.has(edgeKey('industry:food', 'action:BUY'))).toBe(true)
  expect(h.edgeKeys.has(edgeKey('stock:x', 'action:SELL'))).toBe(false)
})

it('builds highlight from evidence without refetch', () => {
  const h = highlightFromEvidence(
    [{ src: 'pattern:momentum_up', dst: 'action:BUY', action: 'BUY' }],
    'BUY',
  )
  expect(h.nodeIds.has('pattern:momentum_up')).toBe(true)
  expect(h.nodeIds.has('action:BUY')).toBe(true)
})

it('hides stock labels in the far camera', () => {
  expect(
    shouldShowLabel({ id: 'stock:1', layer: 'stock', ratio: 1, highlighted: new Set() }),
  ).toBe(false)
  expect(
    shouldShowLabel({ id: 'industry:food', layer: 'industry', ratio: 1, highlighted: new Set() }),
  ).toBe(true)
  expect(
    shouldShowLabel({ id: 'action:BUY', layer: 'action', ratio: 1, highlighted: new Set() }),
  ).toBe(true)
  expect(
    shouldShowLabel({
      id: 'stock:1',
      layer: 'stock',
      ratio: 1,
      highlighted: new Set(['stock:1']),
    }),
  ).toBe(true)
})

it('filters stocks and cold-start edges from the readable view', () => {
  const out = filterVisibleGraph(
    {
      truncated: false,
      node_count: 4,
      edge_count: 3,
      nodes: [
        { id: 'industry:food', layer: 'industry', label: 'food' },
        { id: 'stock:600519.SH', layer: 'stock', label: '600519.SH' },
        { id: 'action:BUY', layer: 'action', label: 'BUY' },
        { id: 'action:HOLD', layer: 'action', label: 'HOLD' },
      ],
      edges: [
        {
          src: 'industry:food',
          dst: 'action:BUY',
          layer: 'industry',
          confidence: 1,
          sample_count: 4,
          last_tick: 1,
        },
        {
          src: 'stock:600519.SH',
          dst: 'action:HOLD',
          layer: 'stock',
          confidence: 0.2,
          sample_count: 3,
          last_tick: 1,
        },
        {
          src: 'industry:food',
          dst: 'action:HOLD',
          layer: 'industry',
          confidence: 0.1,
          sample_count: 0,
          last_tick: 1,
        },
      ],
    },
    { showStocks: false, showColdStart: false },
  )
  expect(out.edges).toHaveLength(1)
  expect(out.edges[0].src).toBe('industry:food')
  expect(out.hiddenStocks).toBe(1)
  expect(out.hiddenColdStart).toBe(1)
})

it('keeps a highlighted stock even when stocks are hidden', () => {
  const out = filterVisibleGraph(
    {
      truncated: false,
      node_count: 3,
      edge_count: 1,
      nodes: [
        { id: 'stock:600519.SH', layer: 'stock', label: '600519.SH' },
        { id: 'action:BUY', layer: 'action', label: 'BUY' },
        { id: 'industry:food', layer: 'industry', label: 'food' },
      ],
      edges: [
        {
          src: 'stock:600519.SH',
          dst: 'action:BUY',
          layer: 'stock',
          confidence: 1,
          sample_count: 2,
          last_tick: 1,
        },
      ],
    },
    {
      showStocks: false,
      showColdStart: false,
      extraNodeIds: new Set(['stock:600519.SH']),
    },
  )
  expect(out.nodes.some((n) => n.id === 'stock:600519.SH')).toBe(true)
  expect(out.edges).toHaveLength(1)
})

it('formats node ids for display', () => {
  expect(formatGraphNodeId('industry:food')).toBe('行业 food')
  expect(formatGraphNodeId('action:BUY')).toBe('买入')
  expect(formatGraphNodeId('stock:600519.SH')).toBe('600519.SH')
  expect(formatGraphAction('HOLD')).toBe('持有')
})

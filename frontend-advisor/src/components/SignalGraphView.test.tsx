import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { GraphViewPayload } from '../api'
import { edgeKey } from '../signalGraphLayout'
import { SignalGraphView } from './SignalGraphView'

const kill = vi.hoisted(() => vi.fn())

vi.mock('sigma', () => ({
  default: class {
    constructor() {}
    getCamera() {
      return { ratio: 1, animatedReset: vi.fn(), setState: vi.fn(), on: vi.fn() }
    }
    on() {}
    refresh() {}
    setSetting() {
      return this
    }
    kill = kill
  },
}))

afterEach(() => {
  cleanup()
  kill.mockClear()
})

const emptyPayload: GraphViewPayload = {
  truncated: false,
  node_count: 0,
  edge_count: 0,
  nodes: [],
  edges: [],
}

const samplePayload: GraphViewPayload = {
  truncated: true,
  node_count: 2,
  edge_count: 1,
  nodes: [
    { id: 'industry:food', layer: 'industry', label: 'food' },
    { id: 'action:BUY', layer: 'action', label: 'BUY' },
  ],
  edges: [
    {
      src: 'industry:food',
      dst: 'action:BUY',
      layer: 'industry',
      confidence: 1.2,
      sample_count: 4,
      last_tick: 8,
    },
  ],
}

it('shows empty copy when the graph has no edges', () => {
  render(
    <SignalGraphView
      payload={emptyPayload}
      error={null}
      highlight={null}
      onSelectNode={() => {}}
      onResetView={() => {}}
    />,
  )
  expect(screen.getByText('图还是空的，先生成或等自进化写入边')).toBeInTheDocument()
})

it('shows truncated notice and kills sigma on unmount', () => {
  const { unmount } = render(
    <SignalGraphView
      payload={samplePayload}
      error={null}
      highlight={{
        nodeIds: new Set(['industry:food', 'action:BUY']),
        edgeKeys: new Set([edgeKey('industry:food', 'action:BUY')]),
      }}
      onSelectNode={() => {}}
      onResetView={() => {}}
    />,
  )
  expect(screen.getByText('节点/边达上限，已按强度截断')).toBeInTheDocument()
  expect(screen.getByText('行业 food → 买入')).toBeInTheDocument()
  unmount()
  expect(kill).toHaveBeenCalled()
})

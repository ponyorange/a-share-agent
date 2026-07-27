import {
  Children,
  cloneElement,
  isValidElement,
  type ReactNode,
} from 'react'
import type { Components } from 'react-markdown'
import { explorerKlineUrl } from './explorerLinks'

/**
 * A-share / ETF / BJ codes commonly mentioned in advisor replies.
 * Avoids broad \\d{6} to reduce false positives (e.g. 202401 year-month).
 */
const SYMBOL_RE =
  /(?<![0-9A-Za-z./])((?:SH|SZ|BJ)?(?:00[0-9]{4}|30[0-9]{4}|43[0-9]{4}|50[0-9]{4}|51[0-9]{4}|52[0-9]{4}|56[0-9]{4}|58[0-9]{4}|60[0-9]{4}|68[0-9]{4}|15[0-9]{4}|16[0-9]{4}|18[0-9]{4}|83[0-9]{4}|87[0-9]{4}|88[0-9]{4}|92[0-9]{4}))(?![0-9A-Za-z.])/gi

export function normalizeLinkedSymbol(raw: string): string {
  const s = raw.trim().toUpperCase()
  for (const prefix of ['SH', 'SZ', 'BJ'] as const) {
    if (s.startsWith(prefix) && s.length > prefix.length) {
      return s.slice(prefix.length)
    }
  }
  return s.replace(/\D/g, '')
}

export function linkifyPlainText(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  SYMBOL_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = SYMBOL_RE.exec(text)) !== null) {
    const raw = match[1]
    const start = match.index
    if (start > last) out.push(text.slice(last, start))
    const symbol = normalizeLinkedSymbol(raw)
    out.push(
      <a
        key={`${symbol}-${start}`}
        className="text-link agent-symbol-link"
        href={explorerKlineUrl(symbol)}
        target="_blank"
        rel="noreferrer"
        title={`查看 ${symbol} K线`}
      >
        {raw}
      </a>,
    )
    last = start + raw.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out.length ? out : [text]
}

const SKIP_TAGS = new Set(['a', 'code', 'pre', 'kbd', 'samp'])

export function linkifyReactNodes(node: ReactNode): ReactNode {
  return Children.map(node, (child) => {
    if (typeof child === 'string' || typeof child === 'number') {
      return linkifyPlainText(String(child))
    }
    if (!isValidElement<{ children?: ReactNode }>(child)) return child
    const type = child.type
    if (typeof type === 'string' && SKIP_TAGS.has(type)) return child
    if (child.props.children == null) return child
    return cloneElement(child, undefined, linkifyReactNodes(child.props.children))
  })
}

/** react-markdown components that auto-link stock codes in text. */
export const agentMarkdownComponents: Components = {
  p: ({ children, node: _n, ...props }) => <p {...props}>{linkifyReactNodes(children)}</p>,
  li: ({ children, node: _n, ...props }) => <li {...props}>{linkifyReactNodes(children)}</li>,
  td: ({ children, node: _n, ...props }) => <td {...props}>{linkifyReactNodes(children)}</td>,
  th: ({ children, node: _n, ...props }) => <th {...props}>{linkifyReactNodes(children)}</th>,
  strong: ({ children, node: _n, ...props }) => (
    <strong {...props}>{linkifyReactNodes(children)}</strong>
  ),
  em: ({ children, node: _n, ...props }) => <em {...props}>{linkifyReactNodes(children)}</em>,
  h1: ({ children, node: _n, ...props }) => <h1 {...props}>{linkifyReactNodes(children)}</h1>,
  h2: ({ children, node: _n, ...props }) => <h2 {...props}>{linkifyReactNodes(children)}</h2>,
  h3: ({ children, node: _n, ...props }) => <h3 {...props}>{linkifyReactNodes(children)}</h3>,
  h4: ({ children, node: _n, ...props }) => <h4 {...props}>{linkifyReactNodes(children)}</h4>,
  blockquote: ({ children, node: _n, ...props }) => (
    <blockquote {...props}>{linkifyReactNodes(children)}</blockquote>
  ),
}

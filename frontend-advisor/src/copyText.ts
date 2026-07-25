/** Copy plain text; Clipboard API first, then execCommand fallback for mobile Safari / insecure contexts. */
export async function copyText(text: string): Promise<void> {
  const value = String(text ?? '')
  if (!value) throw new Error('empty')

  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // fall through to legacy path
    }
  }

  copyTextViaExecCommand(value)
}

function copyTextViaExecCommand(text: string): void {
  if (typeof document === 'undefined') throw new Error('no document')

  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  // Keep in viewport; iOS often ignores off-screen / display:none nodes.
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.width = '1px'
  ta.style.height = '1px'
  ta.style.padding = '0'
  ta.style.border = 'none'
  ta.style.outline = 'none'
  ta.style.boxShadow = 'none'
  ta.style.opacity = '0'
  ta.style.zIndex = '-1'
  document.body.appendChild(ta)

  const selection = document.getSelection()
  const previousRange =
    selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null

  ta.focus()
  ta.select()
  ta.setSelectionRange(0, ta.value.length)

  let ok = false
  try {
    ok = document.execCommand('copy')
  } finally {
    document.body.removeChild(ta)
    if (selection) {
      selection.removeAllRanges()
      if (previousRange) selection.addRange(previousRange)
    }
  }

  if (!ok) throw new Error('execCommand copy failed')
}

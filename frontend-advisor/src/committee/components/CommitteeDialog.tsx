import { useEffect, useRef, type ReactNode } from 'react'

export default function CommitteeDialog({
  title,
  children,
  onClose,
  closeDisabled = false,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  closeDisabled?: boolean
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const closeDisabledRef = useRef(closeDisabled)
  closeDisabledRef.current = closeDisabled

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    if (closeDisabledRef.current) dialogRef.current?.focus()
    else closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !closeDisabledRef.current) onClose()
      if (event.key !== 'Tab') return
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)',
      )
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previouslyFocused?.focus()
    }
  }, [onClose])

  return (
    <div
      className="committee-dialog-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!closeDisabledRef.current) onClose()
      }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="committee-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <button
            ref={closeRef}
            type="button"
            className="btn ghost"
            aria-label={`关闭${title}`}
            disabled={closeDisabled}
            onClick={onClose}
          >
            ×
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

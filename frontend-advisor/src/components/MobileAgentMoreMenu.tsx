import { useEffect, useId, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { AGENT_NAV_LINKS } from './TopbarNav'

export default function MobileAgentMoreMenu({
  onSwitchToBase,
}: {
  onSwitchToBase: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node | null
      if (target && rootRef.current && !rootRef.current.contains(target)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('touchstart', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('touchstart', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="mobile-agent-more" ref={rootRef}>
      {open ? (
        <div
          className="mobile-agent-more-panel"
          id={menuId}
          role="menu"
          aria-label="页面与面板切换"
        >
          <p className="mobile-agent-more-label">面板</p>
          <button
            type="button"
            role="menuitem"
            className="mobile-agent-more-item"
            onClick={() => {
              setOpen(false)
              onSwitchToBase()
            }}
          >
            切换到基础
          </button>
          <p className="mobile-agent-more-label">Agent</p>
          {AGENT_NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              role="menuitem"
              className={({ isActive }) =>
                `mobile-agent-more-item${isActive ? ' active' : ''}`
              }
              onClick={() => setOpen(false)}
            >
              {link.label}
            </NavLink>
          ))}
        </div>
      ) : null}
      <button
        type="button"
        className="mobile-agent-more-trigger"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        更多
      </button>
    </div>
  )
}

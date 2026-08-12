import { useEffect, useId, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'

export type TopbarNavLink = {
  to: string
  label: string
  end?: boolean
}

export const BASE_NAV_LINKS: TopbarNavLink[] = [
  { to: '/', end: true, label: '首页' },
  { to: '/recommendations', label: '今日关注' },
  { to: '/advice', label: '股票诊断' },
  { to: '/portfolio', label: '我的持仓' },
  { to: '/watchlist', label: '我的收藏' },
  { to: '/history', label: '推荐历史' },
  { to: '/paper', label: '模拟盘' },
  { to: '/leaderboard', label: '龙虎榜' },
  { to: '/regime', label: '今日闸门' },
  { to: '/signal-graph', label: '图学习' },
  { to: '/limitup', label: '打板' },
  { to: '/performance', label: '策略表现' },
  { to: '/strategy', label: '我的策略' },
  { to: '/settings', label: '设置' },
]

export const AGENT_NAV_LINKS: TopbarNavLink[] = [
  { to: '/agent', end: true, label: '投研助手' },
  { to: '/agent/jobs', label: '定时任务' },
  { to: '/agent/limitup-promote', label: '打板晋级' },
  { to: '/agent/config', label: 'Agent 配置' },
  { to: '/agent/strategy', label: '策略副驾' },
  { to: '/agent/settings', label: 'DeepSeek 配置' },
]

export default function TopbarNav({
  links,
  ariaLabel,
}: {
  links: TopbarNavLink[]
  ariaLabel: string
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
    <div className="topbar-nav-wrap" ref={rootRef}>
      <div className="topbar-nav-scroll">
        <nav className="nav" aria-label={ariaLabel}>
          {links.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="topbar-nav-fade" aria-hidden="true" />
      <div className="topbar-nav-all">
        <button
          type="button"
          className={`topbar-nav-all-btn${open ? ' is-open' : ''}`}
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-haspopup="menu"
          onClick={() => setOpen((v) => !v)}
        >
          全部
        </button>
        {open ? (
          <div
            className="topbar-nav-all-panel"
            id={menuId}
            role="menu"
            aria-label="全部功能模块"
          >
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                role="menuitem"
                className={({ isActive }) =>
                  `topbar-nav-all-item${isActive ? ' active' : ''}`
                }
                onClick={() => setOpen(false)}
              >
                {link.label}
              </NavLink>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

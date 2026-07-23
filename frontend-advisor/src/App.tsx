import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  AUTH_CHANGED_EVENT,
  clearSession,
  fetchMe,
  getToken,
  getUser,
  type AuthUser,
} from './auth'
import AdvicePage from './pages/AdvicePage'
import AgentChatPage from './pages/AgentChatPage'
import AgentSettingsPage from './pages/AgentSettingsPage'
import AgentStrategyPage from './pages/AgentStrategyPage'
import CommitteePage from './committee/CommitteePage'
import HistoryPage from './pages/HistoryPage'
import LeaderboardPage from './pages/LeaderboardPage'
import LoginPage from './pages/LoginPage'
import PaperPage from './pages/PaperPage'
import PerformancePage from './pages/PerformancePage'
import PortfolioPage from './pages/PortfolioPage'
import RecommendationsPage from './pages/RecommendationsPage'
import StrategyPage from './pages/StrategyPage'

const DISCLAIMER =
  '基于历史量价规则评分，仅供研究参考，不构成投资建议。次日涨跌无法保证。'

type PanelMode = 'base' | 'agent'

const PANEL_KEY = 'advisor_panel_mode'

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(() => getUser())
  const [checking, setChecking] = useState(Boolean(getToken()))
  const navigate = useNavigate()
  const location = useLocation()

  // 以 URL 为准
  const isAgent = location.pathname.startsWith('/agent')
  const isAgentChat =
    location.pathname === '/agent' || location.pathname === '/agent/committee'

  useEffect(() => {
    if (!getToken()) {
      setChecking(false)
      return
    }
    fetchMe()
      .then((res) => setUser(res.user))
      .catch(() => {
        clearSession()
        setUser(null)
      })
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    const handleAuthChanged = () => {
      if (!getToken()) setUser(null)
    }
    window.addEventListener(AUTH_CHANGED_EVENT, handleAuthChanged)
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, handleAuthChanged)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_KEY, isAgent ? 'agent' : 'base')
    } catch {
      /* ignore */
    }
  }, [isAgent])

  function switchPanel(mode: PanelMode) {
    try {
      localStorage.setItem(PANEL_KEY, mode)
    } catch {
      /* ignore */
    }
    if (mode === 'agent') {
      if (!location.pathname.startsWith('/agent')) navigate('/agent')
    } else if (location.pathname.startsWith('/agent')) {
      navigate('/')
    }
  }

  if (checking) {
    return (
      <div className="app-shell">
        <main className="main">
          <p className="status">校验登录…</p>
        </main>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="app-shell">
        <main className="main">
          <LoginPage onAuthed={setUser} />
        </main>
      </div>
    )
  }

  return (
    <div
      className={[
        'app-shell',
        isAgent ? 'app-shell--agent' : '',
        isAgentChat ? 'app-shell--agent-chat' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="panel-switch panel-switch--float" role="tablist" aria-label="面板">
        <button
          type="button"
          role="tab"
          className={`panel-switch-opt${!isAgent ? ' active' : ''}`}
          aria-selected={!isAgent}
          onClick={() => switchPanel('base')}
        >
          基础
        </button>
        <button
          type="button"
          role="tab"
          className={`panel-switch-opt${isAgent ? ' active' : ''}`}
          aria-selected={isAgent}
          onClick={() => switchPanel('agent')}
        >
          Agent
        </button>
      </div>

      <header className="topbar">
        <div className="brand-block">
          <p className="brand">次日顾问</p>
          <p className="brand-sub">规则评分 · AKQuant 校验</p>
        </div>
        <nav className="nav">
          {isAgent ? (
            <>
              <NavLink to="/agent" end>
                投研助手
              </NavLink>
              <NavLink to="/agent/committee">投委会</NavLink>
              <NavLink to="/agent/strategy">策略副驾</NavLink>
              <NavLink to="/agent/settings">DeepSeek 配置</NavLink>
            </>
          ) : (
            <>
              <NavLink to="/" end>
                今日关注
              </NavLink>
              <NavLink to="/advice">标的诊断</NavLink>
              <NavLink to="/portfolio">我的持仓</NavLink>
              <NavLink to="/history">推荐历史</NavLink>
              <NavLink to="/paper">模拟盘</NavLink>
              <NavLink to="/leaderboard">龙虎榜</NavLink>
              <NavLink to="/performance">策略表现</NavLink>
              <NavLink to="/strategy">我的策略</NavLink>
            </>
          )}
        </nav>
        <div className="user-bar">
          <span className="user-name">{user.username}</span>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              clearSession()
              setUser(null)
            }}
          >
            退出
          </button>
        </div>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<RecommendationsPage />} />
          <Route path="/advice" element={<AdvicePage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/leaderboard" element={<LeaderboardPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/strategy" element={<StrategyPage />} />
          <Route path="/agent" element={<AgentChatPage />} />
          <Route path="/agent/committee" element={<CommitteePage />} />
          <Route path="/agent/strategy" element={<AgentStrategyPage />} />
          <Route path="/agent/settings" element={<AgentSettingsPage />} />
          <Route path="/agent/focus" element={<Navigate to="/agent" replace />} />
          <Route path="/agent/*" element={<Navigate to="/agent" replace />} />
        </Routes>
      </main>

      {!isAgentChat ? (
        <footer className="footer">
          <p>{DISCLAIMER}</p>
        </footer>
      ) : null}
    </div>
  )
}

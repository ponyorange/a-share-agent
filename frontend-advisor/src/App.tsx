import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  AUTH_CHANGED_EVENT,
  clearSession,
  fetchMe,
  getToken,
  getUser,
  type AuthUser,
} from './auth'
import MobileAgentMoreMenu from './components/MobileAgentMoreMenu'
import TopbarNav, { AGENT_NAV_LINKS, BASE_NAV_LINKS } from './components/TopbarNav'
import AccountPage from './pages/AccountPage'
import AdvicePage from './pages/AdvicePage'
import AgentChatPage from './pages/AgentChatPage'
import AgentSettingsPage from './pages/AgentSettingsPage'
import AgentStrategyPage from './pages/AgentStrategyPage'
import CommitteePage from './committee/CommitteePage'
import HistoryPage from './pages/HistoryPage'
import KnowledgePage from './pages/KnowledgePage'
import LeaderboardPage from './pages/LeaderboardPage'
import LimitUpPage from './pages/LimitUpPage'
import LoginPage from './pages/LoginPage'
import PaperPage from './pages/PaperPage'
import PerformancePage from './pages/PerformancePage'
import PortfolioPage from './pages/PortfolioPage'
import RecommendationsPage from './pages/RecommendationsPage'
import RegimePage from './pages/RegimePage'
import WatchlistPage from './pages/WatchlistPage'
import MonitorJobsPage from './pages/MonitorJobsPage'
import SettingsPage from './pages/SettingsPage'
import StrategyPage from './pages/StrategyPage'
import { ThemeProvider } from './theme/ThemeProvider'

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
  const isAgentChatPage = location.pathname === '/agent'

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
      <ThemeProvider userId={user?.id ?? null}>
        <div className="app-shell">
          <main className="main">
            <p className="status">校验登录…</p>
          </main>
        </div>
      </ThemeProvider>
    )
  }

  if (!user) {
    return (
      <ThemeProvider userId={null}>
        <div className="app-shell">
          <main className="main">
            <LoginPage onAuthed={setUser} />
          </main>
        </div>
      </ThemeProvider>
    )
  }

  return (
    <ThemeProvider userId={user.id}>
      <div
        className={[
          'app-shell',
          isAgent ? 'app-shell--agent' : '',
          isAgentChat ? 'app-shell--agent-chat' : '',
          isAgentChatPage ? 'app-shell--agent-chat-page' : '',
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
          <TopbarNav
            links={isAgent ? AGENT_NAV_LINKS : BASE_NAV_LINKS}
            ariaLabel={isAgent ? 'Agent 导航' : '基础导航'}
          />
          <div className="user-bar">
            <Link className="user-name" to="/account">
              {user.username}
            </Link>
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
            <Route path="/watchlist" element={<WatchlistPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/paper" element={<PaperPage />} />
            <Route path="/leaderboard" element={<LeaderboardPage />} />
            <Route path="/regime" element={<RegimePage />} />
            <Route path="/limitup" element={<LimitUpPage />} />
            <Route path="/performance" element={<PerformancePage />} />
            <Route path="/strategy" element={<StrategyPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/agent" element={<AgentChatPage />} />
            <Route path="/agent/jobs" element={<MonitorJobsPage />} />
            <Route path="/agent/committee" element={<CommitteePage />} />
            <Route path="/agent/config" element={<KnowledgePage />} />
            <Route
              path="/agent/knowledge"
              element={<Navigate to="/agent/config" replace />}
            />
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

        {isAgentChatPage ? (
          <MobileAgentMoreMenu onSwitchToBase={() => switchPanel('base')} />
        ) : null}
      </div>
    </ThemeProvider>
  )
}

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import FundPage from './FundPage'
import KlinePage from './KlinePage'
import MarketPage from './MarketPage'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DEFAULT_SOURCE } from './sources'
import './styles.css'

const routerBasename = import.meta.env.BASE_URL.replace(/\/$/, '') || undefined

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter basename={routerBasename}>
        <Routes>
          <Route path="/" element={<Navigate to={`/${DEFAULT_SOURCE}`} replace />} />
          <Route path="/market" element={<Navigate to={`/${DEFAULT_SOURCE}/market`} replace />} />
          <Route path="/kline" element={<Navigate to={`/${DEFAULT_SOURCE}/kline`} replace />} />
          <Route path="/fund" element={<Navigate to={`/${DEFAULT_SOURCE}/fund`} replace />} />
          <Route path="/:source" element={<App />} />
          <Route path="/:source/market" element={<MarketPage />} />
          <Route path="/:source/kline" element={<KlinePage />} />
          <Route path="/:source/fund" element={<FundPage />} />
          <Route path="*" element={<Navigate to={`/${DEFAULT_SOURCE}`} replace />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
)

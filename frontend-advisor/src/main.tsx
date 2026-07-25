import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { getUser } from './auth'
import './styles.css'
import { bootstrapTheme } from './theme/themeStorage'

export function initializeTheme() {
  bootstrapTheme(getUser()?.id)
}

initializeTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

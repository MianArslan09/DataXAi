import { createRoot } from 'react-dom/client'
import './styles/base.css'
import './styles/workspace.css'
import './styles/login.css'
import { AuthProvider } from './auth/AuthProvider'
import { App } from './App'
createRoot(document.getElementById('root')!).render(
  <AuthProvider>
    <App />
  </AuthProvider>,
)

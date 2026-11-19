import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { AuthenticatedUser } from '../services/contracts'
import { authService } from '../services/providers'
import { dashboardPath } from './roleAdapter'

type Context = {
  user: AuthenticatedUser | null
  message: string
  login: (name: string, pass: string) => Promise<void>
  logout: () => void
}
const Auth = createContext<Context | null>(null)
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthenticatedUser | null>(null)
  const [message, setMessage] = useState('')
  function end(reason = '') {
    authService.logout()
    setUser(null)
    setMessage(reason)
    window.location.hash = '/login'
  }
  useEffect(() => {
    if (!user) return
    let active = true
    const due = Date.now() + authService.nextCheck()
    let renewing = false
    async function check() {
      if (renewing || Date.now() < due) return
      renewing = true
      try {
        const renewed = await authService.renew()
        if (active) setUser(renewed)
      } catch (err) {
        if (active) end(err instanceof Error ? err.message : 'Session expired. Sign in again.')
      }
    }
    const timer = window.setTimeout(check, Math.max(0, due - Date.now()) + 5)
    window.addEventListener('focus', check)
    return () => {
      active = false
      clearTimeout(timer)
      window.removeEventListener('focus', check)
    }
  }, [user])
  async function login(name: string, pass: string) {
    setMessage('')
    const next = await authService.login(name, pass)
    window.location.hash = dashboardPath(next.appRole)
    setUser(next)
  }
  return (
    <Auth.Provider value={{ user, message, login, logout: () => end() }}>{children}</Auth.Provider>
  )
}
export function useAuth() {
  const auth = useContext(Auth)
  if (!auth) throw new Error('Authentication context unavailable.')
  return auth
}

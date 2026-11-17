import { createContext, useContext, useState, type ReactNode } from 'react'
import type { AuthenticatedUser, Workspace, WorkspaceService } from '../services/contracts'
import { Notice } from './ui'

type Work = {
  user: AuthenticatedUser
  data: Workspace
  service: WorkspaceService
  reload: () => void
}
const Context = createContext<Work | null>(null)
export function WorkspaceProvider({
  user,
  service,
  children,
}: {
  user: AuthenticatedUser
  service: WorkspaceService
  children: ReactNode
}) {
  const [, setRevision] = useState(0)
  let data: Workspace
  try {
    data = service.snapshot(user)
  } catch (err) {
    return (
      <Notice error>{err instanceof Error ? err.message : 'Workspace data is unavailable.'}</Notice>
    )
  }
  return (
    <Context.Provider value={{ user, service, data, reload: () => setRevision((n) => n + 1) }}>
      {children}
    </Context.Provider>
  )
}
export function useWork() {
  const ctx = useContext(Context)
  if (!ctx) throw new Error('Workspace unavailable.')
  return ctx
}

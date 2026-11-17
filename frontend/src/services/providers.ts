import { DjangoAuth } from './apiClient'
import { DemoProvider, demoCredential, demoRoles } from './demo'
import type { AuthService, WorkspaceService } from './contracts'

const mode = import.meta.env.VITE_DATA_MODE ?? 'demo'
if (mode !== 'api' && mode !== 'demo') throw new Error('VITE_DATA_MODE must be demo or api.')
export const dataMode = mode as 'api' | 'demo'
const demo = dataMode === 'demo' ? new DemoProvider() : null
export const authService: AuthService = demo ?? new DjangoAuth()
export const loginPresets = demo ? { ...demoCredential, roles: demoRoles } : null
export const workspaceService: WorkspaceService | null = demo

import type { AuthService } from './contracts'
import { makeUser } from '../auth/identity'

type Json = Record<string, unknown>
async function request(path: string, options: RequestInit = {}): Promise<Json> {
  let response: Response
  try {
    response = await fetch(path, {
      ...options,
      signal: AbortSignal.timeout(12000),
      credentials: 'omit',
      cache: 'no-store',
    })
  } catch {
    throw new Error('Django is unavailable. Check the connection and try again.')
  }
  if (response.status === 401)
    throw new Error(
      path.endsWith('/token/')
        ? 'Username or password is incorrect.'
        : 'Session expired. Sign in again.',
    )
  if (response.status === 403) throw new Error('Permission denied by Django.')
  if (!response.ok) throw new Error('Django could not complete the request. Try again.')
  try {
    return await response.json()
  } catch {
    throw new Error('Unexpected response from Django.')
  }
}
const post = (body: Json) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})
function requireString(value: unknown): string {
  if (typeof value !== 'string' || !value)
    throw new Error('Django returned an incomplete authentication response.')
  return value
}
function expiry(token: string): number {
  try {
    const body = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    if (typeof body.exp === 'number' && Number.isFinite(body.exp)) return body.exp * 1000
  } catch {
    /* The token response must contain an expiry before renewal can be scheduled. */
  }
  throw new Error('Django returned an unreadable token expiry.')
}
export class DjangoAuth implements AuthService {
  private refresh = ''
  private expires = 0
  private generation = 0
  private async accept(pair: Json, generation: number, previousRefresh?: string) {
    const access = requireString(pair.access)
    const refresh = requireString(pair.refresh ?? previousRefresh)
    const expires = expiry(access)
    const me = await request('/api/auth/me/', { headers: { Authorization: 'Bearer ' + access } })
    if ((typeof me.id !== 'number' && typeof me.id !== 'string') || me.is_active !== true)
      throw new Error('Django did not return an active account.')
    const user = makeUser(
      {
        userId: String(me.id),
        username: requireString(me.username),
        fullName: requireString(me.username),
        backendRole: requireString(me.role),
      },
      'api',
    )
    if (generation !== this.generation) throw new Error('Session ended.')
    this.refresh = refresh
    this.expires = expires
    return user
  }
  async login(username: string, password: string) {
    this.logout()
    const generation = this.generation
    return this.accept(await request('/api/auth/token/', post({ username, password })), generation)
  }
  async renew() {
    const generation = this.generation
    const previous = this.refresh
    if (!previous) throw new Error('Session expired. Sign in again.')
    return this.accept(
      await request('/api/auth/token/refresh/', post({ refresh: previous })),
      generation,
      previous,
    )
  }
  nextCheck() {
    return Math.max(500, this.expires - Date.now() - 15000)
  }
  logout() {
    this.generation++
    this.refresh = ''
    this.expires = 0
  }
}

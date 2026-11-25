export const ROLE_MAP = {
  admin: 'Admin',
  data_engineer: 'Manager',
  business_analyst: 'Seller',
} as const
export type BackendRole = keyof typeof ROLE_MAP
export type AppRole = (typeof ROLE_MAP)[BackendRole]

export function mapBackendRole(value: string): AppRole | null {
  return Object.hasOwn(ROLE_MAP, value) ? ROLE_MAP[value as BackendRole] : null
}

export const dashboardPath = (role: AppRole) => '/' + role.toLowerCase() + '/dashboard'

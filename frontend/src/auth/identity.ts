import { mapBackendRole } from './roleAdapter'
import type {
  AuthenticatedUser,
  BusinessContext,
  DataSource,
  Identity,
} from '../services/contracts'

export function makeUser(
  identity: Identity,
  dataSource: DataSource,
  business?: BusinessContext,
): AuthenticatedUser {
  const appRole = mapBackendRole(identity.backendRole)
  if (!appRole) throw new Error('Unsupported role. Contact your administrator for access.')
  return {
    userId: identity.userId,
    username: identity.username,
    fullName: identity.fullName,
    backendRole: identity.backendRole,
    appRole,
    dataSource,
    businessId: business?.businessId ?? null,
    businessName: business?.businessName ?? null,
  }
}

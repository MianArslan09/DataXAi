import { test, expect } from '@playwright/test'
import { dashboardPath, mapBackendRole } from '../src/auth/roleAdapter'
import { makeUser } from '../src/auth/identity'
import { DemoProvider } from '../src/services/demo'
import type { Decision } from '../src/services/contracts'
for (const [backend, app] of Object.entries({
  admin: 'Admin',
  data_engineer: 'Manager',
  business_analyst: 'Seller',
})) {
  test('maps and preserves ' + backend, () => {
    const u = makeUser(
      { userId: '1', username: 'example', fullName: 'Example', backendRole: backend },
      'api',
    )
    expect(u.backendRole).toBe(backend)
    expect(u.appRole).toBe(app)
    expect(dashboardPath(u.appRole)).toBe('/' + app.toLowerCase() + '/dashboard')
    expect(u.businessId).toBeNull()
    expect(u.businessName).toBeNull()
  })
}
test('rejects unknown and prototype property roles', () => {
  for (const value of ['', 'Admin', 'toString', 'constructor', 'owner', '__proto__']) {
    expect(mapBackendRole(value)).toBeNull()
    expect(() =>
      makeUser({ userId: '1', username: 'admin', fullName: 'Admin', backendRole: value }, 'api'),
    ).toThrow(/Unsupported role/)
  }
})
test('tenant and role checks apply to service reads and writes', async () => {
  const service = new DemoProvider(),
    manager = await service.login('manager', 'DataXAi-demo')
  const snapshot = service.snapshot(manager)
  expect(snapshot.submissions.every((s) => s.businessId === 'loom')).toBe(true)
  expect(snapshot.spending).toEqual([])
  expect(snapshot.accounts).toEqual([])
  expect(() => service.advance(manager, 'SUB-1030')).toThrow(/unavailable/)
  await expect(service.submit(manager, 'orders.csv')).rejects.toThrow(/denied/)
  const other = await service.login('bilal', 'DataXAi-demo')
  expect(service.snapshot(other).submissions.map((s) => s.id)).toEqual(['SUB-1030'])
  expect(() => service.snapshot(manager)).toThrow(/denied/)
  expect(() => service.decide(other, 'QR-042', 'Confirm Original')).toThrow(/unavailable/)
  const seller = await service.login('seller', 'DataXAi-demo')
  expect(service.snapshot(seller).quarantine).toEqual([])
  expect(() => service.snapshot({ ...seller, businessId: 'indus' })).toThrow(/denied/)
  service.logout()
  expect(() => service.snapshot(seller)).toThrow(/expired/)
})
for (const decision of [
  'Confirm Original',
  'Authorise Supported Correction',
  'Retain for Review',
] satisfies Decision[]) {
  test(decision + ' preserves invoice and separate seller snapshot', async () => {
    const s = new DemoProvider(),
      user = await s.login('manager', 'DataXAi-demo'),
      before = s.snapshot(user).quarantine[0]
    s.decide(user, before.id, decision)
    const after = s.snapshot(user).quarantine[0]
    expect(after.resolution).toMatchObject({ decision, actor: 'M Arslan Ahmad' })
    expect(after.evidence).toEqual(before.evidence)
    if (decision !== 'Retain for Review')
      expect(() => s.decide(user, before.id, decision)).toThrow()
    const seller = await s.login('seller', 'DataXAi-demo'),
      ex = s.snapshot(seller).explanations[0]
    expect(ex.invoice).toBe('DEMO-INV-042')
    expect(ex.evidence).toEqual(before.evidence)
    expect(ex.evidence.quantity).toBe(3)
    expect(ex.evidence.sourceUnit * 3).toBe(120)
    expect(ex.processedUnit * 3).toBe(12)
    expect((ex.evidence.sourceUnit - ex.processedUnit) * 3).toBe(108)
  })
}

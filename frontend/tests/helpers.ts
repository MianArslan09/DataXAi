import { expect, type Page } from '@playwright/test'
import type { AppRole } from '../src/auth/roleAdapter'
export async function login(page: Page, role: AppRole) {
  await page.getByRole('button', { name: role, exact: true }).click()
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: role + ' dashboard', exact: true })).toBeVisible()
}
export async function nav(page: Page, label: string) {
  await page.getByRole('navigation').getByRole('link', { name: label, exact: true }).click()
}
export async function logout(page: Page) {
  await page.getByRole('button', { name: 'Sign out' }).click()
}
export const apiURL = 'http://127.0.0.1:4188/'

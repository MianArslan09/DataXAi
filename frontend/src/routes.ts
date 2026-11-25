import {
  LayoutDashboard,
  Users,
  UserRoundPlus,
  Building2,
  SlidersHorizontal,
  Inbox,
  Workflow,
  ShieldAlert,
  FileUp,
  ListChecks,
  ChartNoAxesCombined,
  Wallet,
  FileCheck2,
} from 'lucide-react'
import type { AppRole } from './auth/roleAdapter'

const dashboard = { slug: 'dashboard', name: 'Dashboard', icon: LayoutDashboard }
export const navigation = {
  Admin: [
    dashboard,
    { slug: 'users', name: 'Users', icon: Users },
    { slug: 'create-account', name: 'Create Account', icon: UserRoundPlus },
    { slug: 'assignments', name: 'Business Assignments', icon: Building2 },
    { slug: 'configuration', name: 'System Configuration', icon: SlidersHorizontal },
  ],
  Manager: [
    dashboard,
    { slug: 'submissions', name: 'Submissions', icon: Inbox },
    { slug: 'batch', name: 'Batch Processing', icon: Workflow },
    { slug: 'quarantine', name: 'Quarantine Review', icon: ShieldAlert },
  ],
  Seller: [
    dashboard,
    { slug: 'submit', name: 'Submit Data', icon: FileUp },
    { slug: 'status', name: 'Submission Status', icon: ListChecks },
    { slug: 'forecasts', name: 'Forecasts', icon: ChartNoAxesCombined },
    { slug: 'spending', name: 'Customer Spending', icon: Wallet },
    { slug: 'repairs', name: 'Repair Explanations', icon: FileCheck2 },
  ],
} satisfies Record<AppRole, { slug: string; name: string; icon: typeof LayoutDashboard }[]>
export const href = (role: AppRole, slug: string) => '#/' + role.toLowerCase() + '/' + slug

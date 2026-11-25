import { useEffect, useSyncExternalStore } from 'react'
import { LockKeyhole } from 'lucide-react'
import { useAuth } from './auth/AuthProvider'
import { dashboardPath } from './auth/roleAdapter'
import { Card, Empty, Heading, LinkButton } from './components/ui'
import { Shell } from './components/Shell'
import { WorkspaceProvider } from './components/Workspace'
import { workspaceService } from './services/providers'
import { navigation } from './routes'
import { Admin } from './pages/Admin'
import { Manager } from './pages/Manager'
import { Seller } from './pages/Seller'
import { Login } from './pages/Login'

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}
export function App() {
  const { user } = useAuth()
  const hash = useSyncExternalStore(subscribe, () => location.hash.slice(1))
  const [path, query = ''] = hash.split('?')
  const defaultPath = user ? dashboardPath(user.appRole) : '/login'
  const [, role, slug, ...extra] = (path || defaultPath).split('/')
  const valid =
    user &&
    role === user.appRole.toLowerCase() &&
    !extra.length &&
    navigation[user.appRole].some((n) => n.slug === slug)
  useEffect(() => {
    window.scrollTo(0, 0)
    document.title =
      (user
        ? valid
          ? (navigation[user.appRole].find((n) => n.slug === slug)?.name ?? 'Workspace')
          : 'Access denied'
        : 'Sign in') + ' · DataXAi'
  }, [hash, user, valid, slug])
  if (!user) return <Login />
  const missing = user.appRole !== 'Admin' && !user.businessId
  return (
    <Shell slug={valid ? slug : ''}>
      {!valid ? (
        <>
          <Heading title="Access denied" sub="This page is outside your workspace." />
          <LinkButton to={'#' + defaultPath}>Return to dashboard</LinkButton>
        </>
      ) : missing || !workspaceService ? (
        <>
          <Heading
            title={navigation[user.appRole].find((n) => n.slug === slug)!.name}
            sub="Authenticated with Django."
          />
          <Card
            title={missing ? 'Business context unavailable' : 'Integration pending'}
            icon={LockKeyhole}
          >
            <Empty>
              {missing
                ? 'Your identity response has no authorised business assignment.'
                : 'The Django management API for this workflow is unavailable.'}
            </Empty>
          </Card>
        </>
      ) : (
        <WorkspaceProvider user={user} service={workspaceService}>
          {user.appRole === 'Admin' ? (
            <Admin key={slug} slug={slug} />
          ) : user.appRole === 'Manager' ? (
            <Manager key={slug} slug={slug} selected={new URLSearchParams(query).get('id')} />
          ) : (
            <Seller key={slug} slug={slug} selected={new URLSearchParams(query).get('id')} />
          )}
        </WorkspaceProvider>
      )}
    </Shell>
  )
}

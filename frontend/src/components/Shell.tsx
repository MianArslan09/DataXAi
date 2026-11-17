import { useState, type ReactNode } from 'react'
import { Building2, ChevronRight, Database, LogOut, Menu, PanelLeft, X } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { navigation, href } from '../routes'
import { Brand, DemoLabel } from './ui'
export function Shell({ slug, children }: { slug: string; children: ReactNode }) {
  const { user, logout } = useAuth()
  const [menu, setMenu] = useState(false)
  if (!user) return null
  const nav = navigation[user.appRole]
  const current = nav.find((n) => n.slug === slug)
  return (
    <div
      className="shell"
      onKeyDown={(event) => {
        if (menu && event.key === 'Escape') {
          setMenu(false)
          document.getElementById('navigation-toggle')?.focus()
        }
      }}
    >
      <a
        href="#content"
        className="skip"
        onClick={(e) => {
          e.preventDefault()
          document.getElementById('content')?.focus()
        }}
      >
        Skip to content
      </a>
      {menu && (
        <button className="scrim" aria-label="Close navigation" onClick={() => setMenu(false)} />
      )}
      <aside id="workspace-sidebar" className={'sidebar ' + (menu ? 'open' : '')}>
        <a href={href(user.appRole, 'dashboard')} className="brand-link">
          <Brand variant="sidebar" />
        </a>
        <div className="workspace-label">
          <span className="workspace-icon">
            <Building2 size={17} />
          </span>
          <div>
            <small>WORKSPACE</small>
            <b>{user.appRole} console</b>
          </div>
        </div>
        <div className="nav-label">NAVIGATION</div>
        <nav aria-label="Workspace navigation">
          {nav.map((n) => (
            <a
              key={n.slug}
              href={href(user.appRole, n.slug)}
              onClick={() => setMenu(false)}
              aria-current={current?.slug === n.slug ? 'page' : undefined}
            >
              <n.icon size={18} />
              {n.name}
            </a>
          ))}
        </nav>
        <div className="side-bottom">
          <div className="side-mode">
            <i />
            {user.dataSource === 'demo' ? 'Local demonstration' : 'Django connection'}
          </div>
          <button onClick={logout}>
            <LogOut size={17} />
            Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button
            id="navigation-toggle"
            aria-controls="workspace-sidebar"
            aria-expanded={menu}
            className="mobile-menu icon-btn"
            aria-label="Toggle navigation"
            onClick={() => setMenu(!menu)}
          >
            {menu ? <X /> : <Menu />}
          </button>
          <div className="crumb">
            <PanelLeft size={15} />
            <a href={href(user.appRole, 'dashboard')}>{user.appRole} workspace</a>
            <ChevronRight size={13} />
            <span>{current?.name ?? 'Access denied'}</span>
          </div>
          <div className="top-end">
            {user.dataSource === 'demo' ? (
              <DemoLabel />
            ) : (
              <span className="demo-label">
                <Database size={13} />
                Django API
              </span>
            )}
            <div className="business">
              <Building2 size={15} />
              {user.businessName ??
                (user.appRole === 'Admin' ? 'Organisation' : 'Business unavailable')}
            </div>
            <div className="avatar">
              {user.fullName
                .split(' ')
                .map((s) => s[0])
                .slice(0, 2)
                .join('')
                .toUpperCase()}
            </div>
            <div className="profile">
              <b>{user.fullName}</b>
              <small>{user.appRole}</small>
            </div>
          </div>
        </header>
        <main id="content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  )
}

/**
 * Application shell: sidebar, top navigation and breadcrumbs.
 *
 * The navigation is assembled from the signed-in user's role rather than being
 * filtered after the fact, so a customer is never shown a door that the backend
 * would refuse to open. The security-operations entries are the original nine
 * modules, unchanged and unrenamed, so the numbering in the README still matches.
 */
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { useApiResource, useInterval } from '../hooks/useApiResource';
import { formatDateTime, titleCase } from '../lib/format';
import { NotificationPanel } from './notifications';
import { Popover } from './Popover';
import { Badge } from './ui';

export interface NavItem {
  index: string;
  to: string;
  label: string;
  /** True for a section landing page, so it is not also "current" on its children. */
  end?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const BANKING_NAV: NavSection = {
  title: 'Banking',
  items: [
    { index: 'B1', to: '/banking', label: 'Dashboard', end: true },
    { index: 'B2', to: '/banking/transactions', label: 'Transaction History' },
    { index: 'B3', to: '/banking/transfer', label: 'Send Money' },
    { index: 'B4', to: '/banking/security', label: 'Account Security' },
  ],
};

export const SECURITY_NAV: NavSection = {
  title: 'Security Operations',
  items: [
    { index: '01', to: '/overview', label: 'Security Overview', end: true },
    { index: '02', to: '/attack-simulator', label: 'Attack Simulator' },
    { index: '03', to: '/live-monitoring', label: 'Live Monitoring' },
    { index: '04', to: '/transactions', label: 'Transaction Explorer' },
    { index: '05', to: '/network', label: 'Financial Network' },
    { index: '06', to: '/incidents', label: 'Incident Investigation' },
    { index: '07', to: '/defense', label: 'Defense Comparison' },
    { index: '08', to: '/model', label: 'ML Model Evaluation' },
    { index: '09', to: '/settings', label: 'Settings & Documentation' },
  ],
};

/** Friendly titles for the single-segment routes the breadcrumb bar can land on. */
const PAGE_LABELS: Record<string, string> = {
  '/banking': 'Banking Dashboard',
  '/overview': 'Security Overview',
  '/attack-simulator': 'Attack Simulator',
  '/live-monitoring': 'Live Monitoring',
  '/transactions': 'Transaction Explorer',
  '/network': 'Financial Network',
  '/incidents': 'Incident Investigation',
  '/defense': 'Defense Comparison',
  '/model': 'ML Model Evaluation',
  '/settings': 'Settings & Documentation',
};

interface Crumb {
  label: string;
  to?: string;
}

function section(segment: string): { label: string; home: string } {
  if (segment === 'banking') return { label: 'Banking', home: '/banking' };
  if (segment === 'notifications') return { label: 'Notifications', home: '/banking' };
  if (segment === 'transactions') return { label: 'Security Operations', home: '/transactions' };
  return { label: 'Security Operations', home: '/overview' };
}

/** An identifier reads as itself; a word reads as a title. */
function crumbLabel(part: string): string {
  return /\d|-/.test(part) ? part : titleCase(part);
}

function useCrumbs(): Crumb[] {
  const { pathname } = useLocation();
  const parts = pathname.split('/').filter(Boolean);
  if (parts.length === 0) return [{ label: 'RupeeShield' }];

  const root = `/${parts[0]}`;
  if (parts.length === 1) {
    return [{ label: PAGE_LABELS[root] ?? titleCase(parts[0]) }];
  }

  const current = section(parts[0]);
  return [
    { label: current.label, to: current.home },
    ...parts.slice(1).map((part) => ({ label: crumbLabel(part) })),
  ];
}

function Breadcrumbs() {
  const crumbs = useCrumbs();
  return (
    <nav className="topnav__crumbs" aria-label="Breadcrumb">
      <ol className="crumbs">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          return (
            <li key={`${crumb.label}-${index}`}>
              {index > 0 && (
                <span className="crumbs__sep" aria-hidden="true">
                  /
                </span>
              )}
              {crumb.to && !last ? (
                <Link to={crumb.to}>{crumb.label}</Link>
              ) : (
                <span className={last ? 'crumbs__current' : undefined} aria-current={last ? 'page' : undefined}>
                  {crumb.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function UserProfileMenu() {
  const { user, expiresAt, signOut } = useAuth();
  const navigate = useNavigate();

  if (!user) return null;
  const analyst = user.role === 'ANALYST';
  const initials = user.full_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');

  return (
    <Popover
      triggerLabel="Account menu"
      panelClassName="user-menu__panel"
      trigger={() => (
        <>
          <span className="avatar" aria-hidden="true">
            {initials || '?'}
          </span>
          <span className="small">{user.full_name.split(/\s+/)[0]}</span>
        </>
      )}
    >
      {(close) => (
        <>
          <div className="user-menu__meta">
            <div>{user.full_name}</div>
            <div className="mono small dim">{user.email}</div>
            <div className="row" style={{ gap: 6, marginTop: 6 }}>
              <Badge tone={analyst ? 'info' : 'allow'}>{user.role}</Badge>
              {user.is_demo && <Badge tone="neutral">demo</Badge>}
              {!analyst && <Badge tone="neutral">{user.account_count} account(s)</Badge>}
            </div>
            {expiresAt && (
              <div className="small dim" style={{ marginTop: 6 }}>
                Session ends {formatDateTime(expiresAt)}
              </div>
            )}
          </div>

          <button
            type="button"
            className="user-menu__item"
            onClick={() => {
              close();
              navigate(analyst ? '/overview' : '/banking');
            }}
          >
            {analyst ? 'Security overview' : 'My accounts'}
          </button>

          {!analyst && (
            <button
              type="button"
              className="user-menu__item"
              onClick={() => {
                close();
                navigate('/banking/security');
              }}
            >
              Account security
            </button>
          )}

          <button
            type="button"
            className="user-menu__item"
            onClick={() => {
              close();
              void signOut().then(() => navigate('/login', { replace: true }));
            }}
          >
            Sign out
          </button>
        </>
      )}
    </Popover>
  );
}

export function TopNavigation() {
  return (
    <header className="topnav">
      <Breadcrumbs />
      <div className="topnav__actions">
        <NotificationPanel />
        <UserProfileMenu />
      </div>
    </header>
  );
}

export function ApplicationSidebar({ sections }: { sections: NavSection[] }) {
  // Polled so the badge reflects a backend that was restarted while the tab stayed open.
  const health = useApiResource(() => api.health(), []);
  useInterval(health.reload, 15_000);

  return (
    <aside className="sidebar">
      <Link to="/" className="brand" style={{ color: 'inherit', textDecoration: 'none' }}>
        <span className="brand__name">RupeeShield</span>
        <span className="brand__tagline">Financial Cyber Range</span>
      </Link>

      <nav className="nav" aria-label="Primary">
        {sections.map((group) => (
          <div className="nav__section" key={group.title}>
            <span className="nav__section-title">{group.title}</span>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `nav__link ${isActive ? 'nav__link--active' : ''}`}
              >
                <span>{item.label}</span>
                <span className="nav__index">{item.index}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar__footer">
        <div className="row" style={{ gap: 6 }}>
          <span>Backend</span>
          {health.data ? (
            <Badge tone={health.data.status === 'ok' ? 'allow' : 'block'}>{health.data.status}</Badge>
          ) : (
            <Badge tone={health.error ? 'block' : 'neutral'}>
              {health.error ? 'offline' : 'checking'}
            </Badge>
          )}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <span>Models</span>
          <Badge tone={health.data?.models_loaded ? 'info' : 'neutral'}>
            {health.data ? (health.data.models_loaded ? 'trained' : 'absent') : '—'}
          </Badge>
        </div>
        <span>
          Simulated data only. Accounts are scored and flagged for investigation — never labelled
          criminal.
        </span>
      </div>
    </aside>
  );
}

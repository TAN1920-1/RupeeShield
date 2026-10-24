/**
 * Application shell: the nine-item sidebar from the specification plus the route
 * table. Each navigation entry maps to exactly one dashboard area, and the index
 * shown in the sidebar (`01`–`09`) is the same numbering the README uses, so a
 * reader can move between the document and the UI without translating names.
 */
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';

import { api } from './api/client';
import { Badge } from './components/ui';
import { useApiResource, useInterval } from './hooks/useApiResource';
import { AttackSimulatorPage } from './pages/AttackSimulatorPage';
import { DefenseComparisonPage } from './pages/DefenseComparisonPage';
import { FinancialNetworkPage } from './pages/FinancialNetworkPage';
import { IncidentInvestigationPage } from './pages/IncidentInvestigationPage';
import { LiveMonitoringPage } from './pages/LiveMonitoringPage';
import { ModelEvaluationPage } from './pages/ModelEvaluationPage';
import { OverviewPage } from './pages/OverviewPage';
import { SettingsDocsPage } from './pages/SettingsDocsPage';
import { TransactionExplorerPage } from './pages/TransactionExplorerPage';

export interface NavItem {
  index: string;
  to: string;
  label: string;
}

export const NAV_ITEMS: NavItem[] = [
  { index: '01', to: '/overview', label: 'Overview' },
  { index: '02', to: '/attack-simulator', label: 'Attack Simulator' },
  { index: '03', to: '/live-monitoring', label: 'Live Monitoring' },
  { index: '04', to: '/transactions', label: 'Transaction Explorer' },
  { index: '05', to: '/network', label: 'Financial Network' },
  { index: '06', to: '/incidents', label: 'Incident Investigation' },
  { index: '07', to: '/defense', label: 'Defense Comparison' },
  { index: '08', to: '/model', label: 'ML Model Evaluation' },
  { index: '09', to: '/settings', label: 'Settings & Documentation' },
];

function Sidebar() {
  // The health probe is polled so the badge reflects a backend that is restarted
  // while the dashboard stays open.
  const health = useApiResource(() => api.health(), []);
  useInterval(health.reload, 15_000);

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand__name">RupeeShield</span>
        <span className="brand__tagline">Financial Cyber Range</span>
      </div>

      <nav className="nav" aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav__link ${isActive ? 'nav__link--active' : ''}`}
          >
            <span>{item.label}</span>
            <span className="nav__index">{item.index}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar__footer">
        <div className="row" style={{ gap: 6 }}>
          <span>Backend</span>
          {health.data ? (
            <Badge tone={health.data.status === 'ok' ? 'allow' : 'block'}>{health.data.status}</Badge>
          ) : (
            <Badge tone={health.error ? 'block' : 'neutral'}>{health.error ? 'offline' : 'checking'}</Badge>
          )}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <span>Models</span>
          <Badge tone={health.data?.models_loaded ? 'info' : 'neutral'}>
            {health.data ? (health.data.models_loaded ? 'trained' : 'absent') : '—'}
          </Badge>
        </div>
        <span>
          Simulated data only. Accounts are scored and flagged for investigation — never labelled criminal.
        </span>
      </div>
    </aside>
  );
}

export function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Sidebar />
      <main className="main" id="main">
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/attack-simulator" element={<AttackSimulatorPage />} />
          <Route path="/live-monitoring" element={<LiveMonitoringPage />} />
          <Route path="/transactions" element={<TransactionExplorerPage />} />
          <Route path="/network" element={<FinancialNetworkPage />} />
          <Route path="/incidents" element={<IncidentInvestigationPage />} />
          <Route path="/defense" element={<DefenseComparisonPage />} />
          <Route path="/model" element={<ModelEvaluationPage />} />
          <Route path="/settings" element={<SettingsDocsPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </main>
    </div>
  );
}

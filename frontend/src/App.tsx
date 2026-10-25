/**
 * Application shell and route table.
 *
 * Two roles share one application. The sidebar is assembled from the role rather
 * than filtered after the fact, so a customer never sees a security-operations
 * door — and the API refuses one anyway, because every whole-bank module is
 * mounted behind an ANALYST dependency.
 *
 * The security-operations entries keep their original numbering (`01`–`09`), so
 * the README and the sidebar still agree with each other after the banking
 * experience was added beside them.
 */
import { Navigate, Outlet, Route, Routes } from 'react-router-dom';

import { useAuth } from './auth/AuthContext';
import { ProtectedRoute } from './auth/ProtectedRoute';
import {
  ApplicationSidebar,
  BANKING_NAV,
  SECURITY_NAV,
  TopNavigation,
  type NavSection,
} from './components/shell';
import { Card, Loading } from './components/ui';
import { AccountDetailPage } from './pages/AccountDetailPage';
import { AccountSecurityPage } from './pages/AccountSecurityPage';
import { AttackSimulatorPage } from './pages/AttackSimulatorPage';
import { BankingDashboardPage } from './pages/BankingDashboardPage';
import { DefenseComparisonPage } from './pages/DefenseComparisonPage';
import { FinancialNetworkPage } from './pages/FinancialNetworkPage';
import { IncidentInvestigationPage } from './pages/IncidentInvestigationPage';
import { LiveMonitoringPage } from './pages/LiveMonitoringPage';
import { LoginPage } from './pages/LoginPage';
import { ModelEvaluationPage } from './pages/ModelEvaluationPage';
import { OverviewPage } from './pages/OverviewPage';
import { SettingsDocsPage } from './pages/SettingsDocsPage';
import { TransactionExplorerPage } from './pages/TransactionExplorerPage';
import { TransactionHistoryPage } from './pages/TransactionHistoryPage';
import { TransferPage } from './pages/TransferPage';

/**
 * The layout every signed-in page is rendered inside. It answers one question
 * before anything else: is there a session? While the answer is unknown it shows
 * a placeholder rather than a flash of the sign-in form.
 */
function AppLayout() {
  const { status, user } = useAuth();

  if (status === 'checking') {
    return (
      <div className="app-shell">
        <main className="main" id="main">
          <Card>
            <Loading label="Restoring your session…" />
          </Card>
        </main>
      </div>
    );
  }

  if (status !== 'authenticated' || !user) {
    return <Navigate to="/login" replace />;
  }

  const sections: NavSection[] = user.role === 'ANALYST' ? [SECURITY_NAV] : [BANKING_NAV];

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <ApplicationSidebar sections={sections} />
      <div className="workspace">
        <TopNavigation />
        <main className="main" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** Sends each role to the area it can actually use. */
function RoleLanding() {
  const { user } = useAuth();
  return <Navigate to={user?.role === 'ANALYST' ? '/overview' : '/banking'} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<AppLayout />}>
        <Route path="/" element={<RoleLanding />} />

        {/* Customer banking. The role guard here only decides what renders; the
            API re-checks ownership of every account and transaction it returns. */}
        <Route
          path="/banking"
          element={
            <ProtectedRoute role="CUSTOMER">
              <BankingDashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/banking/accounts/:accountId"
          element={
            <ProtectedRoute role="CUSTOMER">
              <AccountDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/banking/transactions"
          element={
            <ProtectedRoute role="CUSTOMER">
              <TransactionHistoryPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/banking/transfer"
          element={
            <ProtectedRoute role="CUSTOMER">
              <TransferPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/banking/security"
          element={
            <ProtectedRoute role="CUSTOMER">
              <AccountSecurityPage />
            </ProtectedRoute>
          }
        />

        {/* Security operations — the original nine modules, unchanged. */}
        <Route
          path="/overview"
          element={
            <ProtectedRoute role="ANALYST">
              <OverviewPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/attack-simulator"
          element={
            <ProtectedRoute role="ANALYST">
              <AttackSimulatorPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/live-monitoring"
          element={
            <ProtectedRoute role="ANALYST">
              <LiveMonitoringPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/transactions"
          element={
            <ProtectedRoute role="ANALYST">
              <TransactionExplorerPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/network"
          element={
            <ProtectedRoute role="ANALYST">
              <FinancialNetworkPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/incidents"
          element={
            <ProtectedRoute role="ANALYST">
              <IncidentInvestigationPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/defense"
          element={
            <ProtectedRoute role="ANALYST">
              <DefenseComparisonPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/model"
          element={
            <ProtectedRoute role="ANALYST">
              <ModelEvaluationPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute role="ANALYST">
              <SettingsDocsPage />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<RoleLanding />} />
      </Route>
    </Routes>
  );
}

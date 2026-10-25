/**
 * Customer banking dashboard.
 *
 * The landing page for the customer role. Every number here is read from the
 * accounts table through `/api/banking/accounts` — a completed transfer changes
 * the balance shown here and the ledger row in the same commit, because they are
 * the same record. Nothing on this page is a fixture.
 */
import { useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type { AccountCard as AccountCardModel, CustomerTransaction } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import {
  AccountCard,
  AlertCard,
  BalanceSummary,
  SecurityStatusBadge,
  TransactionTable,
} from '../components/banking';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  PageHeader,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';
import { formatDateTime } from '../lib/format';

export function BankingDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const accounts = useApiResource(() => api.bankingAccounts(), []);
  const recent = useApiResource(() => api.bankingTransactions({ limit: 5 }), []);
  const security = useApiResource(() => api.securityCentre(), []);

  const firstName = user?.full_name.split(/\s+/)[0] ?? 'there';
  const summary = accounts.data?.summary;

  function openAccount(account: AccountCardModel) {
    navigate(`/banking/accounts/${account.id}`);
  }

  function openTransaction(row: CustomerTransaction) {
    navigate(`/banking/transactions?selected=${encodeURIComponent(row.id)}`);
  }

  return (
    <div className="stack">
      <PageHeader
        title={`Welcome back, ${firstName}`}
        description={
          <>
            Your accounts, your ledger and the risk engine's view of both. Balances are read from the
            accounts table, so a transfer you complete here is reflected everywhere at once.
          </>
        }
        actions={
          <Button variant="primary" onClick={() => navigate('/banking/transfer')}>
            Send money
          </Button>
        }
      />

      {accounts.loading && !accounts.data && <LoadingState label="Loading your accounts…" />}
      {accounts.error && <ErrorState message={accounts.error} onRetry={accounts.reload} />}

      {summary && <BalanceSummary summary={summary} />}

      <Card
        title="Your accounts"
        subtitle="Account numbers are masked; the full number is never generated."
      >
        {accounts.data && accounts.data.items.length === 0 && (
          <EmptyState
            title="No accounts"
            message="No accounts are attached to this login. Run the seeding command to create the demo bank."
          />
        )}
        {accounts.data && accounts.data.items.length > 0 && (
          <div className="grid grid--thirds">
            {accounts.data.items.map((account) => (
              <AccountCard key={account.id} account={account} onOpen={openAccount} />
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Recent activity"
        subtitle="The last five rows on your ledger."
        actions={
          <Button size="small" onClick={() => navigate('/banking/transactions')}>
            Full history
          </Button>
        }
      >
        <TransactionTable
          transactions={recent.data?.items ?? []}
          loading={recent.loading}
          error={recent.error}
          onRetry={recent.reload}
          onSelect={openTransaction}
        />
      </Card>

      <div className="grid grid--halves">
        <AlertCard
          title="Account security"
          tone={
            security.data?.status.level === 'ELEVATED'
              ? 'danger'
              : security.data?.status.level === 'ATTENTION'
                ? 'warning'
                : 'info'
          }
          actions={
            <Button size="small" onClick={() => navigate('/banking/security')}>
              Open security centre
            </Button>
          }
        >
          {security.loading && !security.data && <LoadingState label="Loading security posture…" />}
          {security.error && <ErrorState message={security.error} onRetry={security.reload} />}
          {security.data && (
            <>
              <div className="row" style={{ gap: 8 }}>
                <Badge
                  tone={
                    security.data.status.level === 'ELEVATED'
                      ? 'block'
                      : security.data.status.level === 'ATTENTION'
                        ? 'flag'
                        : 'allow'
                  }
                >
                  {security.data.status.label}
                </Badge>
                {security.data.open_incidents.length > 0 && (
                  <Badge tone="block">{security.data.open_incidents.length} open incident(s)</Badge>
                )}
              </div>
              <p className="small dim" style={{ margin: 0 }}>
                {security.data.status.summary}
              </p>
            </>
          )}
        </AlertCard>

        <Card title="What the engine has queried" subtitle="Rows the detector did not allow outright.">
          {accounts.data && (
            <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {accounts.data.items.map((account) => (
                <li key={account.id} className="row row--between" style={{ gap: 8 }}>
                  <span className="small">
                    {account.label}{' '}
                    <span className="mono dim">{account.masked_number}</span>
                  </span>
                  <span className="row" style={{ gap: 6 }}>
                    <Badge tone={account.suspicious_count > 0 ? 'flag' : 'allow'}>
                      {account.suspicious_count} queried
                    </Badge>
                    <SecurityStatusBadge status={account.status} label={account.status_label} />
                  </span>
                </li>
              ))}
            </ul>
          )}
          {accounts.data && accounts.data.items.every((account) => account.last_activity_at === null) && (
            <Notice>No transactions have been recorded on these accounts yet.</Notice>
          )}
          {accounts.data && (
            <p className="small dim" style={{ margin: 0 }}>
              Last activity:{' '}
              {formatDateTime(
                accounts.data.items
                  .map((account) => account.last_activity_at)
                  .filter((value): value is string => Boolean(value))
                  .sort()
                  .pop() ?? null,
              )}
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

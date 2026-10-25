/**
 * One account, in detail.
 *
 * Reached from the dashboard and from a transaction's account column. The account
 * id is taken from the URL and passed straight to the API, which re-checks that it
 * belongs to the signed-in customer: an id that is not theirs returns 404 rather
 * than an empty page, so this view cannot be used to probe for other accounts.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { CustomerTransaction } from '../api/types';
import {
  AccountCard,
  SecurityStatusBadge,
  TransactionTable,
} from '../components/banking';
import {
  Button,
  Card,
  ErrorState,
  KeyValue,
  LoadingState,
  PageHeader,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';
import { UNKNOWN, formatDateTime, formatMinor, formatMoney, titleCase } from '../lib/format';

const PAGE_SIZE = 25;

export function AccountDetailPage() {
  const { accountId = '' } = useParams();
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);

  const account = useApiResource(() => api.bankingAccount(accountId), [accountId]);
  const ledger = useApiResource(
    () => api.bankingTransactions({ account_id: accountId, limit: PAGE_SIZE, offset }),
    [accountId, offset],
  );

  const total = ledger.data?.total ?? 0;

  function openTransaction(row: CustomerTransaction) {
    navigate(`/banking/transactions?selected=${encodeURIComponent(row.id)}`);
  }

  return (
    <div className="stack">
      <PageHeader
        title={account.data?.label ?? 'Account'}
        description="The account record, its balance and the ledger it produced. Masked number, opening balance and age all come from the stored account row."
        actions={
          <Button onClick={() => navigate('/banking')}>Back to dashboard</Button>
        }
      />

      {account.loading && !account.data && <LoadingState label="Loading account…" />}
      {account.error && <ErrorState message={account.error} onRetry={account.reload} />}

      {account.data && (
        <div className="grid grid--halves">
          <AccountCard account={account.data} />
          <Card title="Account record" subtitle="As stored, with nothing recomputed in the browser.">
            <KeyValue
              items={[
                { label: 'Account ID', value: <span className="mono">{account.data.id}</span> },
                { label: 'Masked number', value: <span className="mono">{account.data.masked_number}</span> },
                { label: 'Type', value: account.data.account_type },
                { label: 'Currency', value: account.data.currency },
                {
                  label: 'Status',
                  value: (
                    <SecurityStatusBadge status={account.data.status} label={account.data.status_label} />
                  ),
                },
                { label: 'Risk status', value: titleCase(account.data.risk_status) },
                { label: 'Balance', value: formatMoney(account.data.balance) },
                { label: 'Opening balance', value: formatMinor(account.data.opening_balance_minor) },
                { label: 'Home country', value: account.data.home_country },
                { label: 'Preferred channel', value: account.data.preferred_channel },
                { label: 'Account age', value: `${account.data.account_age_days} day(s)` },
                { label: 'Transactions', value: String(account.data.transaction_count) },
                { label: 'Queried by the detector', value: String(account.data.suspicious_count) },
                { label: 'Awaiting review', value: String(account.data.pending_count) },
                {
                  label: 'Last activity',
                  value: account.data.last_activity_at
                    ? formatDateTime(account.data.last_activity_at)
                    : UNKNOWN,
                },
                {
                  label: 'Data provenance',
                  value: account.data.is_synthetic ? 'Synthetic demo account' : 'Seeded account',
                },
              ]}
            />
          </Card>
        </div>
      )}

      <Card
        title="Account ledger"
        subtitle="Every row recorded against this account, newest first."
      >
        <TransactionTable
          transactions={ledger.data?.items ?? []}
          loading={ledger.loading}
          error={ledger.error}
          onRetry={ledger.reload}
          onSelect={openTransaction}
        />
        <div className="pagination">
          <span>
            Showing {ledger.data?.returned ?? 0} of {total} row(s)
          </span>
          <div className="row">
            <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </Button>
            <Button
              size="small"
              disabled={total <= offset + PAGE_SIZE}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

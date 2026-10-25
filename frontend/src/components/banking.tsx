/**
 * Customer-banking presentation components.
 *
 * Every figure rendered here comes from the API; nothing is computed in the
 * browser from a fixture and nothing is invented to fill a gap. Where the backend
 * reports "not scored" or "no detection", that is what is shown — an absent
 * detection is never drawn as a risk score of zero.
 */
import type { ReactNode } from 'react';
import type {
  AccountCard as AccountCardModel,
  BalanceSummary as BalanceSummaryModel,
  CustomerTransaction,
  CustomerTransactionDetail,
  ShapFeature,
} from '../api/types';
import {
  UNKNOWN,
  featureLabel,
  formatDateTime,
  formatMetric,
  formatMinor,
  formatMoney,
  riskTone,
  titleCase,
  type Tone,
} from '../lib/format';
import type { Column } from './ui';
import {
  Badge,
  Button,
  Card,
  DataTable,
  KeyValue,
  Notice,
  StatCard,
} from './ui';

/** The band a risk score falls into, in words, so the badge is not colour-only. */
function riskLevel(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return 'Not scored';
  if (score >= 0.8) return 'High';
  if (score >= 0.45) return 'Medium';
  return 'Low';
}

export function RiskScoreBadge({
  score,
  decision,
}: {
  score: number | null | undefined;
  decision?: string | null;
}) {
  if (score === null || score === undefined) {
    return <Badge tone="neutral">{UNKNOWN} not scored</Badge>;
  }
  return (
    <Badge tone={riskTone(score)}>
      {formatMetric(score, 3)} · {riskLevel(score)}
      {decision ? ` · ${decision}` : ''}
    </Badge>
  );
}

/**
 * Settlement / account posture.
 *
 * `label` is the customer-facing wording the backend supplies (Completed,
 * Pending, Blocked, Under Review, Flagged) and `tone` may override the tone the
 * status alone would imply — a settled-but-queried payment reads as Flagged.
 */
export function SecurityStatusBadge({
  status,
  label,
  tone,
}: {
  status: string;
  label?: string;
  tone?: Tone;
}) {
  const derived: Tone =
    status === 'BLOCKED'
      ? 'block'
      : status === 'PENDING_REVIEW' || status === 'UNDER_REVIEW'
        ? 'flag'
        : status === 'SETTLED' || status === 'ACTIVE'
          ? 'allow'
          : 'neutral';
  return <Badge tone={tone ?? derived}>{label ?? titleCase(status)}</Badge>;
}

export function BalanceSummary({ summary }: { summary: BalanceSummaryModel }) {
  return (
    <div className="balance-summary">
      <StatCard
        label="Total balance"
        value={formatMoney(summary.total_balance)}
        hint={`Across ${summary.account_count} account(s) · ${summary.currency}`}
      />
      <StatCard
        label="Held for review"
        value={formatMoney(summary.held)}
        hint="Outgoing value awaiting a decision; it has not left the account."
        tone={summary.held > 0 ? 'flag' : 'neutral'}
      />
      <StatCard
        label="Available"
        value={formatMoney(summary.available)}
        hint="Total balance minus the value currently held."
      />
    </div>
  );
}

export function AccountCard({
  account,
  onOpen,
}: {
  account: AccountCardModel;
  onOpen?: (account: AccountCardModel) => void;
}) {
  return (
    <Card
      className={`account-card ${account.is_primary ? 'account-card--primary' : ''}`}
      title={account.label}
      subtitle={
        <span className="account-card__number">
          {account.masked_number} · {account.account_type}
        </span>
      }
      actions={
        <SecurityStatusBadge status={account.status} label={account.status_label} />
      }
    >
      <div>
        <div className="account-card__balance">{formatMoney(account.balance)}</div>
        <div className="small dim">
          Opened with {formatMinor(account.opening_balance_minor)} · {account.account_age_days} day(s) old
        </div>
      </div>

      <div className="row" style={{ gap: 8 }}>
        <Badge tone="neutral">{account.home_country}</Badge>
        <Badge tone="neutral">{account.preferred_channel}</Badge>
        {account.risk_status !== 'NORMAL' && (
          <Badge tone="flag">{titleCase(account.risk_status)}</Badge>
        )}
        {account.is_primary && <Badge tone="info">Primary</Badge>}
        {account.is_synthetic && <Badge tone="neutral">Synthetic</Badge>}
      </div>

      <div className="small dim">
        {account.transaction_count} transaction(s) · {account.suspicious_count} queried by the detector
        {account.pending_count > 0 ? ` · ${account.pending_count} awaiting review` : ''}
        {account.last_activity_at ? ` · last activity ${formatDateTime(account.last_activity_at)}` : ''}
      </div>

      {onOpen && (
        <div className="row">
          <Button size="small" onClick={() => onOpen(account)}>
            View account
          </Button>
        </div>
      )}
    </Card>
  );
}

export function TransactionTable({
  transactions,
  onSelect,
  loading,
  error,
  onRetry,
  caption,
}: {
  transactions: CustomerTransaction[];
  onSelect?: (row: CustomerTransaction) => void;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  caption?: string;
}) {
  const columns: Column<CustomerTransaction>[] = [
    {
      key: 'id',
      header: 'Transaction ID',
      render: (row) => <span className="mono">{row.id}</span>,
    },
    { key: 'date', header: 'Date', render: (row) => formatDateTime(row.timestamp) },
    { key: 'description', header: 'Description', render: (row) => row.description },
    {
      key: 'sender',
      header: 'Sender',
      render: (row) => <span className="mono">{row.sender ?? UNKNOWN}</span>,
    },
    {
      key: 'recipient',
      header: 'Recipient',
      render: (row) => <span className="mono">{row.recipient ?? UNKNOWN}</span>,
    },
    { key: 'type', header: 'Type', render: (row) => titleCase(row.direction) },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      render: (row) => (
        <span className={`txn-amount txn-amount--${row.direction === 'CREDIT' ? 'credit' : 'debit'}`}>
          {row.direction === 'CREDIT' ? '+' : '−'}
          {formatMinor(row.amount_minor)}
        </span>
      ),
    },
    {
      key: 'account',
      header: 'Account',
      render: (row) => (
        <span className="mono">
          {row.account_label ?? row.account_id} {row.account_masked_number}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <SecurityStatusBadge
          status={row.status}
          label={row.status_label}
          tone={row.suspicious && row.status === 'SETTLED' ? 'flag' : undefined}
        />
      ),
    },
    {
      key: 'risk',
      header: 'Risk',
      render: (row) => <RiskScoreBadge score={row.risk_score} decision={row.decision} />,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={transactions}
      keyFor={(row) => row.id}
      loading={loading}
      error={error}
      onRetry={onRetry}
      onRowClick={onSelect}
      caption={caption}
      emptyTitle="No transactions"
      emptyMessage="Nothing matches the current filters."
    />
  );
}

export function TransactionDetails({ detail }: { detail: CustomerTransactionDetail }) {
  const shapColumns: Column<ShapFeature>[] = [
    { key: 'feature', header: 'Feature', render: (row) => featureLabel(row.feature) },
    { key: 'value', header: 'Value', numeric: true, render: (row) => formatMetric(row.value, 3) },
    {
      key: 'contribution',
      header: 'Contribution',
      numeric: true,
      render: (row) => formatMetric(row.shap_contribution, 4),
    },
  ];

  return (
    <div className="stack">
      <KeyValue
        items={[
          { label: 'Transaction ID', value: <span className="mono">{detail.id}</span> },
          { label: 'Date', value: formatDateTime(detail.timestamp) },
          { label: 'Description', value: detail.description },
          { label: 'Type', value: titleCase(detail.direction) },
          { label: 'Sender', value: <span className="mono">{detail.sender ?? UNKNOWN}</span> },
          { label: 'Recipient', value: <span className="mono">{detail.recipient ?? UNKNOWN}</span> },
          {
            label: 'Account',
            value: (
              <span className="mono">
                {detail.account_label ?? detail.account_id} {detail.account_masked_number}
              </span>
            ),
          },
          {
            label: 'Amount',
            value: (
              <span className={`txn-amount txn-amount--${detail.direction === 'CREDIT' ? 'credit' : 'debit'}`}>
                {formatMinor(detail.amount_minor)} {detail.currency}
              </span>
            ),
          },
          { label: 'Channel', value: detail.channel },
          {
            label: 'Status',
            value: (
              <SecurityStatusBadge
                status={detail.status}
                label={detail.status_label}
                tone={detail.suspicious && detail.status === 'SETTLED' ? 'flag' : undefined}
              />
            ),
          },
          {
            label: 'Risk score',
            value: <RiskScoreBadge score={detail.risk_score} decision={detail.decision} />,
          },
          {
            label: 'Detection',
            value: detail.detection_id ? <span className="mono">{detail.detection_id}</span> : UNKNOWN,
          },
        ]}
      />

      {detail.status === 'BLOCKED' && (
        <AlertCard title="This payment was blocked" tone="danger">
          <p className="small" style={{ margin: 0 }}>
            The automated defense stopped the payment before the money moved. No debit was applied to
            the account.
          </p>
        </AlertCard>
      )}

      <div>
        <h3>Why the detector reacted</h3>
        {detail.detection ? (
          <KeyValue
            items={Object.entries(detail.detection).map(([key, value]) => ({
              label: titleCase(key),
              value:
                value === null || value === undefined ? (
                  UNKNOWN
                ) : typeof value === 'object' ? (
                  <code className="mono">{JSON.stringify(value)}</code>
                ) : (
                  <span className="mono">{String(value)}</span>
                ),
            }))}
          />
        ) : (
          <Notice>No detection record exists for this transaction — it was never queried.</Notice>
        )}
      </div>

      <div>
        <h3>Feature contributions</h3>
        <DataTable
          columns={shapColumns}
          rows={detail.explanation}
          keyFor={(row) => row.feature}
          emptyTitle="No explanation"
          emptyMessage="No SHAP features were recorded for this transaction."
        />
      </div>

      <div>
        <h3>Correlated security events</h3>
        {detail.security_events.length === 0 ? (
          <Notice>No security events were correlated with this transaction.</Notice>
        ) : (
          <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {detail.security_events.map((entry, index) => (
              <li key={String(entry.id ?? index)} className="factor">
                <div className="row" style={{ gap: 8 }}>
                  <Badge tone="neutral">{String(entry.event_type ?? 'EVENT')}</Badge>
                  <span className="small">{String(entry.description ?? '')}</span>
                </div>
                <span className="small dim">
                  {formatDateTime(entry.timestamp as string | undefined)}
                  {entry.device_id ? ` · device ${String(entry.device_id)}` : ''}
                  {entry.country ? ` · ${String(entry.country)}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3>Incident</h3>
        {detail.incident ? (
          <AlertCard
            title={detail.incident.title}
            tone={detail.incident.severity === 'CRITICAL' || detail.incident.severity === 'HIGH' ? 'danger' : 'warning'}
          >
            <KeyValue
              items={[
                { label: 'Incident ID', value: <span className="mono">{detail.incident.id}</span> },
                { label: 'Status', value: detail.incident.status },
                { label: 'Severity', value: <Badge tone="flag">{detail.incident.severity}</Badge> },
                { label: 'Risk level', value: titleCase(detail.incident.risk_level) },
                {
                  label: 'Scenario',
                  value: detail.incident.scenario_name || titleCase(detail.incident.scenario ?? ''),
                },
                { label: 'Opened', value: formatDateTime(detail.incident.opened_at) },
                { label: 'Events', value: String(detail.incident.event_count) },
                { label: 'Transactions', value: String(detail.incident.transaction_count) },
                { label: 'Suspected loss', value: formatMoney(detail.incident.suspected_loss) },
              ]}
            />
            <p className="small dim" style={{ margin: 0 }}>
              This is the same incident record the security-operations console investigates; a customer
              sees the summary, not the investigation payload.
            </p>
          </AlertCard>
        ) : (
          <Notice>This transaction was not correlated into an incident.</Notice>
        )}
      </div>

      <div>
        <h3>Related activity</h3>
        {detail.related.length === 0 ? (
          <Notice>No related rows were recorded.</Notice>
        ) : (
          <DataTable
            columns={[
              {
                key: 'record',
                header: 'Related record',
                render: (row) => <code className="mono small">{JSON.stringify(row.row)}</code>,
              },
            ]}
            rows={detail.related.map((row, index) => ({ key: `${String(row.id ?? 'row')}-${index}`, row }))}
            keyFor={(row) => row.key}
            emptyTitle="No related activity"
            emptyMessage="No related rows were recorded."
          />
        )}
      </div>
    </div>
  );
}

export function AlertCard({
  title,
  tone = 'info',
  actions,
  children,
}: {
  title: ReactNode;
  tone?: 'info' | 'warning' | 'danger';
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className={`alert-card alert-card--${tone}`} title={title} actions={actions}>
      {children}
    </Card>
  );
}

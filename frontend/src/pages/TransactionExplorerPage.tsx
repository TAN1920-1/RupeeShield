/**
 * Transaction Explorer (Module E).
 *
 * The ledger of simulated transactions plus the per-transaction detection
 * breakdown: the risk score, the defense decision and, when the transaction was
 * flagged, the correlated security events and SHAP explanation behind that
 * decision. Everything here is simulated — the simulator generates the rows.
 */
import { useCallback, useState } from 'react';

import { api } from '../api/client';
import type { ShapFeature, Transaction, TransactionDetail } from '../api/types';
import {
  Badge,
  Button,
  Card,
  DataTable,
  DecisionBadge,
  EmptyState,
  ErrorState,
  Field,
  KeyValue,
  Loading,
  Notice,
  PageHeader,
  StatusBadge,
  type Column,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';
import {
  UNKNOWN,
  featureLabel,
  formatDateTime,
  formatMetric,
  formatMinor,
  riskTone,
  titleCase,
} from '../lib/format';

type Json = Record<string, unknown>;

const TRANSACTION_STATUSES = ['SETTLED', 'FLAGGED', 'BLOCKED', 'PENDING'];

const LIMIT_OPTIONS = [25, 50, 100, 200];

const asText = (value: unknown): string =>
  value === null || value === undefined
    ? UNKNOWN
    : typeof value === 'string'
      ? value
      : typeof value === 'number' || typeof value === 'boolean'
        ? String(value)
        : JSON.stringify(value);

const jsonRowKey = (row: Json): string => {
  const id = row.id;
  return typeof id === 'string' ? id : JSON.stringify(row);
};

/**
 * Build columns from a preferred key list: only keys actually present in at least
 * one row are shown. When none of them appear, fall back to the whole record as
 * JSON so no data is hidden.
 */
function columnsFromKeys(rows: Json[], keys: string[]): Column<Json>[] {
  const present = keys.filter((key) => rows.some((row) => key in row));
  if (present.length === 0) {
    return [
      {
        key: '__raw',
        header: 'Record',
        render: (row) => <code className="mono small">{JSON.stringify(row)}</code>,
      },
    ];
  }
  return present.map((key) => ({
    key,
    header: titleCase(key),
    render: (row) => <span className="mono small">{asText(row[key])}</span>,
  }));
}

const SECURITY_EVENT_KEYS = ['timestamp', 'event_type', 'severity', 'description', 'detail'];

function renderNested(value: unknown) {
  if (value === null || value === undefined) return UNKNOWN;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return <span className="mono">{String(value)}</span>;
  return <code className="mono">{JSON.stringify(value)}</code>;
}

export function TransactionExplorerPage() {
  const [accountId, setAccountId] = useState('');
  const [runId, setRunId] = useState('');
  const [decision, setDecision] = useState('');
  const [status, setStatus] = useState('');
  const [scenario, setScenario] = useState('');
  const [minAmount, setMinAmount] = useState('');
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const scenarios = useApiResource(() => api.scenarios(), []);

  const list = useApiResource(
    () =>
      api.transactions({
        account_id: accountId || undefined,
        run_id: runId || undefined,
        decision: decision || undefined,
        status: status || undefined,
        scenario: scenario || undefined,
        min_amount: minAmount === '' ? undefined : Number(minAmount),
        limit,
        offset,
      }),
    [accountId, runId, decision, status, scenario, minAmount, limit, offset],
  );

  const detail = useApiResource<TransactionDetail | null>(
    () => (selectedId ? api.transaction(selectedId) : Promise.resolve(null)),
    [selectedId],
  );

  const selectTransaction = useCallback((row: Transaction) => {
    setSelectedId(row.id);
  }, []);

  const columns: Column<Transaction>[] = [
    { key: 'id', header: 'Transaction', render: (row) => <span className="mono">{row.id}</span> },
    { key: 'timestamp', header: 'Timestamp', render: (row) => formatDateTime(row.timestamp) },
    {
      key: 'account',
      header: 'Account',
      render: (row) => <span className="mono">{row.account_label ?? row.account_id}</span>,
    },
    {
      key: 'counterparty',
      header: 'Counterparty',
      render: (row) => <span className="mono">{row.counterparty_label ?? row.counterparty_id ?? UNKNOWN}</span>,
    },
    { key: 'amount', header: 'Amount', numeric: true, render: (row) => formatMinor(row.amount_minor) },
    { key: 'channel', header: 'Channel', render: (row) => row.channel },
    { key: 'country', header: 'Country', render: (row) => row.country },
    {
      key: 'risk',
      header: 'Risk',
      numeric: true,
      render: (row) => <Badge tone={riskTone(row.risk_score)}>{formatMetric(row.risk_score, 3)}</Badge>,
    },
    { key: 'decision', header: 'Decision', render: (row) => <DecisionBadge decision={row.decision} /> },
    { key: 'status', header: 'Status', render: (row) => <StatusBadge status={row.status} /> },
    {
      key: 'scenario',
      header: 'Scenario',
      render: (row) => (row.scenario ? titleCase(row.scenario) : UNKNOWN),
    },
  ];

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

  const selected = detail.data;
  const securityRows: Json[] = selected?.security_events ?? [];
  const neighborhoodRows: Json[] = selected?.neighborhood ?? [];
  const neighborhoodKeys = Array.from(new Set(neighborhoodRows.flatMap((row) => Object.keys(row))));

  return (
    <div className="stack">
      <PageHeader
        title="Transaction Explorer"
        description="Simulated transactions produced by the attack simulator. Each row carries a risk score, a defense decision and — when it was flagged — the correlated security events behind that decision."
      />

      <Card title="Filters">
        <div className="filters">
          <Field label="Account ID">
            <input
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
              placeholder="optional"
            />
          </Field>
          <Field label="Run ID">
            <input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="optional" />
          </Field>
          <Field label="Decision">
            <select value={decision} onChange={(event) => setDecision(event.target.value)}>
              <option value="">All decisions</option>
              <option value="ALLOW">ALLOW</option>
              <option value="FLAG">FLAG</option>
              <option value="BLOCK">BLOCK</option>
            </select>
          </Field>
          <Field label="Status">
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              {TRANSACTION_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Scenario">
            <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
              <option value="">All scenarios</option>
              {(scenarios.data?.items ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Minimum amount (INR)">
            <input
              type="number"
              min={0}
              value={minAmount}
              onChange={(event) => setMinAmount(event.target.value)}
              placeholder="optional"
            />
          </Field>
          <Field label="Page size">
            <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
              {LIMIT_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      <Card title="Ledger" subtitle="Click a row to open its detection breakdown.">
        <DataTable
          columns={columns}
          rows={list.data?.items ?? []}
          keyFor={(row) => row.id}
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          onRowClick={selectTransaction}
          emptyTitle="No transactions"
          emptyMessage="Nothing matches the current filters."
        />
        <div className="row row--between">
          <span className="small dim">
            Showing {list.data?.returned ?? 0} of {list.data?.total ?? 0}
          </span>
          <div className="row">
            <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              Previous
            </Button>
            <Button
              size="small"
              disabled={(list.data?.total ?? 0) <= offset + limit}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      {selectedId && (
        <Card
          title={`Transaction ${selectedId}`}
          actions={
            <Button size="small" variant="ghost" onClick={() => setSelectedId(null)}>
              Close
            </Button>
          }
        >
          {detail.loading && <Loading label="Loading transaction…" />}
          {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}
          {!detail.loading && !detail.error && !selected && (
            <EmptyState title="No detail" message="No detail record was returned for this transaction." />
          )}

          {selected && !detail.loading && !detail.error && (
            <div className="stack">
              <KeyValue
                items={[
                  { label: 'Transaction ID', value: <span className="mono">{selected.id}</span> },
                  {
                    label: 'Account',
                    value: <span className="mono">{selected.account_label ?? selected.account_id}</span>,
                  },
                  {
                    label: 'Counterparty',
                    value: (
                      <span className="mono">
                        {selected.counterparty_label ?? selected.counterparty_id ?? UNKNOWN}
                      </span>
                    ),
                  },
                  { label: 'Amount', value: formatMinor(selected.amount_minor) },
                  { label: 'Currency', value: selected.currency },
                  { label: 'Channel', value: selected.channel },
                  { label: 'Country', value: selected.country },
                  { label: 'Merchant', value: <span className="mono">{selected.merchant_id}</span> },
                  { label: 'Device', value: <span className="mono">{selected.device_id}</span> },
                  { label: 'Direction', value: selected.direction },
                  { label: 'Status', value: <StatusBadge status={selected.status} /> },
                  { label: 'Scenario', value: selected.scenario ? titleCase(selected.scenario) : UNKNOWN },
                  {
                    label: 'Risk score',
                    value: <Badge tone={riskTone(selected.risk_score)}>{formatMetric(selected.risk_score, 3)}</Badge>,
                  },
                  { label: 'Decision', value: <DecisionBadge decision={selected.decision} /> },
                  {
                    label: 'Fraud (simulated label)',
                    value: selected.is_fraud === null ? UNKNOWN : selected.is_fraud ? 'Yes' : 'No',
                  },
                ]}
              />

              <div>
                <h3>Detection</h3>
                {selected.detection ? (
                  <KeyValue
                    items={Object.entries(selected.detection).map(([key, value]) => ({
                      label: titleCase(key),
                      value: renderNested(value),
                    }))}
                  />
                ) : (
                  <Notice>No detection record exists for this transaction.</Notice>
                )}
              </div>

              <div>
                <h3>SHAP explanation</h3>
                <DataTable
                  columns={shapColumns}
                  rows={selected.explanation}
                  keyFor={(row) => row.feature}
                  emptyTitle="No explanation"
                  emptyMessage="No SHAP features were recorded for this transaction."
                />
              </div>

              <div>
                <h3>Security events</h3>
                <DataTable
                  columns={columnsFromKeys(securityRows, SECURITY_EVENT_KEYS)}
                  rows={securityRows}
                  keyFor={jsonRowKey}
                  emptyTitle="No security events"
                  emptyMessage="No correlated security events for this transaction."
                />
              </div>

              <div>
                <h3>Neighborhood</h3>
                <DataTable
                  columns={columnsFromKeys(neighborhoodRows, neighborhoodKeys)}
                  rows={neighborhoodRows}
                  keyFor={jsonRowKey}
                  emptyTitle="No neighborhood"
                  emptyMessage="No related accounts were recorded."
                />
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

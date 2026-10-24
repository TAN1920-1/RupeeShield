/**
 * Financial Network (Module F).
 *
 * The money-flow graph between simulated accounts plus the derived tables that
 * make the picture readable without the canvas: the highest-outbound accounts and
 * the largest relationships, and a per-account drill-down.
 *
 * Wording rule followed everywhere on this page: an account is described as
 * "flagged for investigation" or as carrying "elevated simulated risk" — never as
 * criminal or a fraudster.
 */
import { useCallback, useState } from 'react';

import { api } from '../api/client';
import type { AccountEventsPayload } from '../api/client';
import type { GraphEdge, GraphNode } from '../api/types';
import { NetworkGraphView } from '../components/NetworkGraphView';
import {
  Badge,
  Button,
  Card,
  DataTable,
  ErrorState,
  Field,
  KeyValue,
  Legend,
  Loading,
  Notice,
  PageHeader,
  StatCard,
  type Column,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';
import {
  UNKNOWN,
  formatDateTime,
  formatMetric,
  formatMinor,
  formatNumber,
  riskStatusLabel,
  titleCase,
  type Tone,
} from '../lib/format';

type Json = Record<string, unknown>;

const NODE_LIMITS = [50, 100, 150, 250, 400];

const LEGEND_PALETTE = ['#35c46a', '#f5b942', '#ef4d4d'];

const DEFAULT_LEGEND = [
  { label: 'Normal', colour: '#35c46a' },
  { label: 'Flagged for investigation', colour: '#f5b942' },
  { label: 'Elevated simulated risk', colour: '#ef4d4d' },
];

const SECURITY_EVENT_KEYS = ['timestamp', 'event_type', 'severity', 'description', 'detail'];

const TRANSACTION_KEYS = [
  'timestamp',
  'amount_minor',
  'direction',
  'channel',
  'country',
  'status',
  'decision',
  'risk_score',
];

const INCIDENT_KEYS = ['id', 'title', 'severity', 'status', 'opened_at', 'summary'];

const riskStatusTone = (status: string): Tone => {
  if (status === 'FLAGGED_FOR_INVESTIGATION') return 'flag';
  if (status === 'ELEVATED_SIMULATED_RISK') return 'block';
  return 'allow';
};

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

export function FinancialNetworkPage() {
  const [runId, setRunId] = useState('');
  const [incidentId, setIncidentId] = useState('');
  const [limit, setLimit] = useState(150);
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);

  const graph = useApiResource(
    () =>
      api.networkGraph({
        run_id: runId || undefined,
        incident_id: incidentId || undefined,
        limit,
      }),
    [runId, incidentId, limit],
  );

  const accountEvents = useApiResource<AccountEventsPayload | null>(
    () => (selectedAccountId ? api.accountEvents(selectedAccountId) : Promise.resolve(null)),
    [selectedAccountId],
  );

  const onSelectNode = useCallback((accountId: string) => setSelectedAccountId(accountId), []);

  const data = graph.data;
  const nodes = data?.nodes ?? [];
  const edges = data?.edges ?? [];
  const suspiciousEdges = edges.filter((edge) => edge.suspicious).length;

  const topOutbound = [...nodes].sort((a, b) => b.total_out_minor - a.total_out_minor).slice(0, 15);
  const topEdges = [...edges].sort((a, b) => b.amount_minor - a.amount_minor).slice(0, 15);

  const nodeColumns: Column<GraphNode>[] = [
    { key: 'id', header: 'Account', render: (node) => <span className="mono">{node.id}</span> },
    { key: 'label', header: 'Label', render: (node) => node.label },
    {
      key: 'risk_status',
      header: 'Risk status',
      render: (node) => (
        <Badge tone={riskStatusTone(node.risk_status)}>{riskStatusLabel(node.risk_status)}</Badge>
      ),
    },
    {
      key: 'transactions',
      header: 'Transactions',
      numeric: true,
      render: (node) => formatNumber(node.transaction_count),
    },
    {
      key: 'outbound',
      header: 'Outbound',
      numeric: true,
      render: (node) => formatMinor(node.total_out_minor),
    },
    { key: 'inbound', header: 'Inbound', numeric: true, render: (node) => formatMinor(node.total_in_minor) },
    {
      key: 'suspicious',
      header: 'Suspicious connections',
      numeric: true,
      render: (node) => formatNumber(node.suspicious_connection_count),
    },
    { key: 'balance', header: 'Balance', numeric: true, render: (node) => formatMinor(node.balance_minor) },
  ];

  const edgeColumns: Column<GraphEdge>[] = [
    { key: 'source', header: 'Source', render: (edge) => <span className="mono">{edge.source}</span> },
    { key: 'target', header: 'Target', render: (edge) => <span className="mono">{edge.target}</span> },
    {
      key: 'transactions',
      header: 'Transactions',
      numeric: true,
      render: (edge) => formatNumber(edge.transaction_count),
    },
    { key: 'amount', header: 'Amount', numeric: true, render: (edge) => formatMinor(edge.amount_minor) },
    {
      key: 'suspicious',
      header: 'Suspicious',
      render: (edge) => (
        <Badge tone={edge.suspicious ? 'block' : 'neutral'}>{edge.suspicious ? 'Suspicious' : 'Normal'}</Badge>
      ),
    },
    {
      key: 'max_risk',
      header: 'Max risk score',
      numeric: true,
      render: (edge) => formatMetric(edge.max_risk_score),
    },
  ];

  const payload = accountEvents.data;

  return (
    <div className="stack">
      <PageHeader
        title="Financial Network"
        description="Money-flow between simulated accounts, derived from the same synthetic ledger. Accounts are flagged for investigation or show elevated simulated risk — never labelled criminal."
      />

      <Card title="Filters">
        <div className="filters">
          <Field label="Run ID">
            <input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="optional" />
          </Field>
          <Field label="Incident ID">
            <input
              value={incidentId}
              onChange={(event) => setIncidentId(event.target.value)}
              placeholder="optional"
            />
          </Field>
          <Field label="Node limit">
            <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
              {NODE_LIMITS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      {graph.error && <ErrorState message={graph.error} onRetry={graph.reload} />}
      {graph.loading && !data && <Loading label="Loading network…" />}

      {data && (
        <>
          <Card title="Account graph" subtitle="Click a node to inspect that account.">
            <NetworkGraphView graph={data} onSelectNode={onSelectNode} />
            <Legend
              items={
                data.legend.length > 0
                  ? data.legend.map((label, index) => ({
                      label,
                      colour: LEGEND_PALETTE[index % LEGEND_PALETTE.length],
                    }))
                  : DEFAULT_LEGEND
              }
            />
            {data.truncated && (
              <Notice tone="warning">
                The graph was limited to {formatNumber(data.node_limit)} nodes. Apply a narrower run or incident
                filter to see the complete network.
              </Notice>
            )}
          </Card>

          <div className="grid grid--stats">
            <StatCard label="Accounts" value={formatNumber(nodes.length)} />
            <StatCard label="Relationships" value={formatNumber(edges.length)} />
            <StatCard
              label="Suspicious relationships"
              value={formatNumber(suspiciousEdges)}
              tone={suspiciousEdges > 0 ? 'flag' : 'neutral'}
            />
            <StatCard label="Generated" value={formatDateTime(data.generated_at)} />
          </div>

          <Card title="Highest outbound accounts">
            <DataTable
              columns={nodeColumns}
              rows={topOutbound}
              keyFor={(node) => node.id}
              loading={graph.loading}
              emptyTitle="No accounts"
              emptyMessage="No accounts match the current filter."
            />
          </Card>

          <Card title="Largest relationships">
            <DataTable
              columns={edgeColumns}
              rows={topEdges}
              keyFor={(edge) => edge.id}
              loading={graph.loading}
              emptyTitle="No relationships"
              emptyMessage="No relationships match the current filter."
            />
          </Card>
        </>
      )}

      {selectedAccountId && (
        <Card
          title="Account detail"
          subtitle={<span className="mono">{selectedAccountId}</span>}
          actions={
            <Button size="small" variant="ghost" onClick={() => setSelectedAccountId(null)}>
              Close
            </Button>
          }
        >
          {accountEvents.loading && !payload && <Loading label="Loading account…" />}
          {accountEvents.error && <ErrorState message={accountEvents.error} onRetry={accountEvents.reload} />}

          {payload && (
            <div className="stack">
              <KeyValue
                items={[
                  { label: 'Account ID', value: <span className="mono">{payload.account.id}</span> },
                  { label: 'Label', value: payload.account.label },
                  {
                    label: 'Risk status',
                    value: (
                      <Badge tone={riskStatusTone(payload.account.risk_status)}>
                        {riskStatusLabel(payload.account.risk_status)}
                      </Badge>
                    ),
                  },
                  { label: 'Primary account', value: payload.account.is_primary ? 'Yes' : 'No' },
                  { label: 'Balance', value: formatMinor(payload.account.balance_minor) },
                  { label: 'Transactions', value: formatNumber(payload.account.transaction_count) },
                  { label: 'Outbound total', value: formatMinor(payload.account.total_out_minor) },
                  { label: 'Inbound total', value: formatMinor(payload.account.total_in_minor) },
                  {
                    label: 'Suspicious connections',
                    value: formatNumber(payload.account.suspicious_connection_count),
                  },
                ]}
              />

              <div>
                <h3>Observations</h3>
                {payload.observations.length > 0 ? (
                  <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                    {payload.observations.map((note, index) => (
                      <li key={index}>{note}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="dim small">No observations recorded for this account.</p>
                )}
              </div>

              <div>
                <h3>Security events</h3>
                <DataTable
                  columns={columnsFromKeys(payload.security_events, SECURITY_EVENT_KEYS)}
                  rows={payload.security_events}
                  keyFor={jsonRowKey}
                  emptyTitle="No security events"
                  emptyMessage="No security events recorded for this account."
                />
              </div>

              <div>
                <h3>Transactions</h3>
                <DataTable
                  columns={columnsFromKeys(payload.transactions, TRANSACTION_KEYS)}
                  rows={payload.transactions}
                  keyFor={jsonRowKey}
                  emptyTitle="No transactions"
                  emptyMessage="No transactions recorded for this account."
                />
              </div>

              <div>
                <h3>Incidents</h3>
                <DataTable
                  columns={columnsFromKeys(payload.incidents, INCIDENT_KEYS)}
                  rows={payload.incidents}
                  keyFor={jsonRowKey}
                  emptyTitle="No incidents"
                  emptyMessage="No incidents reference this account."
                />
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

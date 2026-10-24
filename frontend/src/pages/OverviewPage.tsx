/**
 * Module A — Security Overview.
 *
 * The landing page answers "what has happened so far?" using only stored rows:
 * every figure comes from `/api/overview`, which computes them from the database in
 * the request itself. Nothing on this page is hard-coded, and each statistic carries
 * a `provenance.source` so an observed-dataset number is never mistaken for a
 * simulation or model number.
 */
import { Badge, Card, DataTable, Notice, PageHeader, StatCard, StatusBadge, type Column } from '../components/ui';
import { BreakdownChart, TrendChart } from '../components/charts';
import { api } from '../api/client';
import type { Incident, OverviewPayload } from '../api/types';
import { useApiResource, useInterval } from '../hooks/useApiResource';
import {
  formatDateTime,
  formatMetric,
  formatMinor,
  formatNumber,
  formatTime,
  severityTone,
  titleCase,
  UNKNOWN,
} from '../lib/format';

type Json = Record<string, unknown>;

const asText = (value: unknown): string => {
  if (value === null || value === undefined) return UNKNOWN;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
};

const DECISION_COLOURS = ['#35c46a', '#f5b942', '#ef4d4d', '#6c7d97'];
const SEVERITY_COLOURS = ['#6c7d97', '#22d3ee', '#f5b942', '#ef4d4d', '#ef4d4d'];

/**
 * `DataTable` keys rows with a single-argument `keyFor`, and these rows are untyped
 * server payloads, so we inject a private `__key` field and hide it from the columns.
 */
function keyedRows(rows: Json[]): Json[] {
  return rows.map((row, index) => ({ ...row, __key: asText(row.id ?? row.sequence ?? `row-${index}`) }));
}

/** Derive a stable column set from the union of keys across untyped rows. */
function jsonColumns(rows: Json[]): Column<Json>[] {
  const keys: string[] = [];
  rows.forEach((row) => {
    Object.keys(row).forEach((key) => {
      if (key.startsWith('__')) return;
      if (!keys.includes(key)) keys.push(key);
    });
  });
  return keys.map((key) => ({
    key,
    header: titleCase(key),
    numeric: rows.every((row) => typeof row[key] === 'number'),
    render: (row: Json) => <span className="small">{asText(row[key])}</span>,
  }));
}

export function OverviewPage() {
  const overview = useApiResource(() => api.overview(), []);
  // A dashboard left open on a second monitor should not go stale, but it must not
  // poll aggressively either: 20s is comfortably above the simulation tick rate.
  useInterval(overview.reload, 20_000);

  const payload: OverviewPayload | null = overview.data;
  const stats = payload?.stats;
  const topAccounts = keyedRows(payload?.charts.top_risk_accounts ?? []);
  const recentEvents = keyedRows(payload?.recent_events ?? []);

  const incidentColumns: Column<Incident>[] = [
    { key: 'id', header: 'Incident', render: (row) => <span className="mono">{row.id}</span> },
    { key: 'title', header: 'Title', render: (row) => row.title },
    { key: 'severity', header: 'Severity', render: (row) => <Badge tone={severityTone(row.severity)}>{row.severity}</Badge> },
    { key: 'status', header: 'Status', render: (row) => <StatusBadge status={row.status} /> },
    { key: 'loss', header: 'Suspected loss', numeric: true, render: (row) => formatMinor(row.suspected_loss_minor) },
    { key: 'opened', header: 'Opened', render: (row) => formatDateTime(row.opened_at) },
  ];

  return (
    <>
      <PageHeader
        title="Security Overview"
        description={
          <>
            Every number on this page is computed from rows stored in the local database —{' '}
            {stats ? `refreshed ${formatDateTime(stats.generated_at)}` : 'loading'}. Ground-truth labels come from the
            synthetic generator, so metrics describe a simulation, not a real bank.
          </>
        }
      />

      {overview.error && <Notice tone="warning">Could not load the overview: {overview.error}</Notice>}

      {stats && (
        <>
          <div className="grid grid--stats">
            <StatCard
              label="Simulated transactions"
              value={formatNumber(stats.total_simulated_transactions)}
              hint={`${formatNumber(stats.blocked_transactions)} blocked · ${formatNumber(stats.flagged_transactions)} flagged`}
            />
            <StatCard
              label="Simulated value"
              value={formatMinor(stats.total_value_minor)}
              tone="info"
              hint="Total amount moved in the simulation"
            />
            <StatCard
              label="Suspicious transactions"
              value={formatNumber(stats.suspicious_transactions)}
              tone="flag"
              hint="Flagged or blocked by the detection engine"
            />
            <StatCard
              label="Value at risk"
              value={formatMinor(stats.value_at_risk_minor)}
              tone="flag"
              hint="Amount on transactions that were flagged or blocked"
            />
            <StatCard
              label="Loss prevented (simulated)"
              value={formatMinor(stats.loss_prevented_minor)}
              tone="allow"
              hint="Value the defense stopped before settlement"
            />
            <StatCard
              label="Realized loss (simulated)"
              value={formatMinor(stats.realized_loss_minor)}
              tone="block"
              hint="Value that settled on transactions the defense missed"
            />
            <StatCard
              label="Open incidents"
              value={formatNumber(stats.open_incidents)}
              tone="flag"
              hint={`${formatNumber(stats.total_incidents)} incidents in total`}
            />
            <StatCard
              label="Runs"
              value={formatNumber(stats.active_runs + stats.completed_runs)}
              hint={`${formatNumber(stats.active_runs)} active · ${formatNumber(stats.completed_runs)} completed`}
            />
            <StatCard
              label="Detection latency p95"
              value={
                stats.detection_latency.p95_ms === null
                  ? UNKNOWN
                  : `${formatMetric(stats.detection_latency.p95_ms, 2)} ms`
              }
              hint={
                stats.detection_latency.mean_ms === null
                  ? 'No scored transactions yet'
                  : `mean ${formatMetric(stats.detection_latency.mean_ms, 2)} ms over ${formatNumber(
                      stats.detection_latency.samples,
                    )} samples`
              }
            />
          </div>

          {payload && payload.empty_states.length > 0 && (
            <Notice tone="warning">
              <strong>Why some figures are zero:</strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {payload.empty_states.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </Notice>
          )}

          {payload && (
            <div className="grid grid--halves">
              <Card title="Transaction volume over time" subtitle="Simulated transactions per bucket, oldest first">
                <TrendChart
                  points={payload.charts.volume_over_time.map((point) => ({
                    label: formatTime(point.timestamp),
                    value: point.value,
                  }))}
                />
              </Card>
              <Card title="Decisions" subtitle="How the detection engine responded">
                <BreakdownChart
                  points={payload.charts.decisions_breakdown.map((row) => ({ label: titleCase(row.key), value: row.count }))}
                  colours={DECISION_COLOURS}
                />
              </Card>
              <Card title="Alerts by severity" subtitle="Correlated security events grouped by severity">
                <BreakdownChart
                  points={payload.charts.alerts_by_severity.map((row) => ({ label: titleCase(row.key), value: row.count }))}
                  colours={SEVERITY_COLOURS}
                />
              </Card>
              <Card title="Attack scenarios exercised" subtitle="Runs grouped by the scenario they exercised">
                <BreakdownChart
                  points={payload.charts.scenario_breakdown.map((row) => ({ label: titleCase(row.key), value: row.count }))}
                />
              </Card>
            </div>
          )}

          {topAccounts.length > 0 && (
            <Card
              title="Highest-risk accounts"
              subtitle="Ranked by accumulated simulated risk. Accounts are flagged for investigation, never labelled criminal."
            >
              <DataTable<Json>
                columns={jsonColumns(topAccounts)}
                rows={topAccounts}
                keyFor={(row) => asText(row.__key)}
              />
            </Card>
          )}

          <Card title="Recent incidents" subtitle="Correlated clusters waiting for an investigator">
            <DataTable<Incident>
              columns={incidentColumns}
              rows={payload?.recent_incidents ?? []}
              keyFor={(row) => row.id}
              loading={overview.loading}
              error={overview.error}
              onRetry={overview.reload}
              emptyTitle="No incidents yet"
              emptyMessage="Run an attack scenario from the Attack Simulator to raise incidents."
            />
          </Card>

          {recentEvents.length > 0 && (
            <Card title="Recent security events" subtitle="Raw events as produced by the simulation engine">
              <DataTable<Json>
                columns={jsonColumns(recentEvents)}
                rows={recentEvents}
                keyFor={(row) => asText(row.__key)}
              />
            </Card>
          )}

          {stats && (
            <Card
              title="Where these numbers come from"
              subtitle="Each statistic declares its provenance so the four kinds of figure are never confused"
            >
              <DataTable<{ key: string; source: string; detail: string; deterministic: string }>
                columns={[
                  { key: 'key', header: 'Statistic', render: (row) => <span className="mono">{row.key}</span> },
                  { key: 'source', header: 'Source', render: (row) => <Badge tone="info">{row.source}</Badge> },
                  { key: 'detail', header: 'Detail', render: (row) => <span className="small">{row.detail}</span> },
                  { key: 'deterministic', header: 'Deterministic', render: (row) => row.deterministic },
                ]}
                rows={Object.entries(stats.provenance).map(([key, value]) => ({
                  key,
                  source: value.source,
                  detail: value.detail,
                  deterministic: value.deterministic === null ? UNKNOWN : value.deterministic ? 'yes' : 'no',
                }))}
                keyFor={(row) => row.key}
              />
            </Card>
          )}

          {stats && stats.detection_latency.samples > 0 && (
            <Card title="Detection latency percentiles" subtitle="Measured on this machine, so treat as indicative">
              <DataTable<{ label: string; value: string }>
                columns={[
                  { key: 'label', header: 'Percentile', render: (row) => row.label },
                  { key: 'value', header: 'Latency', numeric: true, render: (row) => row.value },
                ]}
                rows={[
                  {
                    label: 'p50',
                    value:
                      stats.detection_latency.p50_ms === null ? UNKNOWN : `${formatMetric(stats.detection_latency.p50_ms, 2)} ms`,
                  },
                  {
                    label: 'p95',
                    value:
                      stats.detection_latency.p95_ms === null ? UNKNOWN : `${formatMetric(stats.detection_latency.p95_ms, 2)} ms`,
                  },
                  {
                    label: 'p99',
                    value:
                      stats.detection_latency.p99_ms === null ? UNKNOWN : `${formatMetric(stats.detection_latency.p99_ms, 2)} ms`,
                  },
                  { label: 'Samples', value: formatNumber(stats.detection_latency.samples) },
                ]}
                keyFor={(row) => row.label}
              />
            </Card>
          )}
        </>
      )}

      {overview.loading && !stats && (
        <Card title="Loading overview">
          <span className="rows-loading">
            <span className="spinner" aria-hidden="true" />
            Reading stored simulation rows…
          </span>
        </Card>
      )}
    </>
  );
}

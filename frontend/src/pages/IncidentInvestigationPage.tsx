/**
 * Incident Investigation (Modules G + H).
 *
 * An incident is a correlated cluster of simulated security events and
 * transactions that has been raised for investigation. Accounts referenced here
 * are synthetic and are only ever "flagged for investigation" / at "elevated
 * simulated risk" — never labelled criminal.
 *
 * The replay panel is deliberately non-destructive: it reproduces the timeline
 * from stored inputs and reports divergences, but it never mutates the stored
 * incident history (the backend reports `mutates_history: false`).
 */
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { Incident, IncidentDetail, Replay, ReplayStep } from '../api/types';
import { IncidentTimeline } from '../components/IncidentTimeline';
import {
  Badge,
  Button,
  Card,
  DataTable,
  DecisionBadge,
  ErrorState,
  Field,
  KeyValue,
  Loading,
  Notice,
  PageHeader,
  StatusBadge,
} from '../components/ui';
import type { Column } from '../components/ui';
import {
  UNKNOWN,
  formatDateTime,
  formatMetric,
  formatMinor,
  formatNumber,
  severityTone,
  titleCase,
} from '../lib/format';
import { useApiResource } from '../hooks/useApiResource';

const STATUSES = ['OPEN', 'INVESTIGATING', 'CONTAINED', 'CLOSED'] as const;
const LIMIT_OPTIONS = [25, 50, 100] as const;

type Json = Record<string, unknown>;

/** Renders any JSON leaf as readable text; objects fall back to JSON. */
const asText = (value: unknown): string => {
  if (value === null || value === undefined) return UNKNOWN;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
};

/** Builds columns from the union of keys actually present in the rows. */
function jsonColumns(rows: Json[]): Column<Json>[] {
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys.map((key) => ({
    key,
    header: titleCase(key),
    render: (row: Json) => asText(row[key]),
  }));
}

function rowKey(row: Json): string {
  const id = row.id;
  if (typeof id === 'string') return id;
  if (typeof id === 'number') return String(id);
  return JSON.stringify(row);
}

const INCIDENT_COLUMNS: Column<Incident>[] = [
  { key: 'id', header: 'ID', render: (row) => <span className="mono">{row.id}</span> },
  { key: 'title', header: 'Title', render: (row) => row.title },
  {
    key: 'scenario',
    header: 'Scenario',
    render: (row) => row.scenario_name ?? titleCase(row.scenario),
  },
  {
    key: 'severity',
    header: 'Severity',
    render: (row) => <Badge tone={severityTone(row.severity)}>{row.severity}</Badge>,
  },
  { key: 'status', header: 'Status', render: (row) => <StatusBadge status={row.status} /> },
  {
    key: 'account',
    header: 'Primary account',
    render: (row) => (
      <span className="mono">{row.primary_account_label ?? row.primary_account_id}</span>
    ),
  },
  { key: 'opened', header: 'Opened', render: (row) => formatDateTime(row.opened_at) },
  {
    key: 'events',
    header: 'Events',
    render: (row) => formatNumber(row.event_count),
    numeric: true,
  },
  {
    key: 'transactions',
    header: 'Transactions',
    render: (row) => formatNumber(row.transaction_count),
    numeric: true,
  },
  {
    key: 'loss',
    header: 'Suspected loss',
    render: (row) => formatMinor(row.suspected_loss_minor),
    numeric: true,
  },
];

const REPLAY_STEP_COLUMNS: Column<ReplayStep>[] = [
  { key: 'seq', header: 'Seq', render: (step) => formatNumber(step.seq), numeric: true },
  { key: 'timestamp', header: 'Timestamp', render: (step) => formatDateTime(step.timestamp) },
  { key: 'kind', header: 'Kind', render: (step) => step.kind },
  { key: 'description', header: 'Description', render: (step) => step.description },
  {
    key: 'decision',
    header: 'Decision',
    render: (step) => <DecisionBadge decision={step.decision} />,
  },
  {
    key: 'risk',
    header: 'Risk score',
    render: (step) => formatMetric(step.risk_score, 3),
    numeric: true,
  },
  {
    key: 'matches',
    header: 'Matches original',
    render: (step) => (
      <Badge tone={step.matches_original ? 'allow' : 'block'}>
        {step.matches_original ? 'match' : 'diverged'}
      </Badge>
    ),
  },
];

export function IncidentInvestigationPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [scenarioFilter, setScenarioFilter] = useState('');
  const [limit, setLimit] = useState<number>(25);
  const [offset, setOffset] = useState(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const [replay, setReplay] = useState<Replay | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  const scenarios = useApiResource(() => api.scenarios(), []);

  const incidents = useApiResource(
    () => api.incidents({ status: statusFilter, scenario: scenarioFilter, limit, offset }),
    [statusFilter, scenarioFilter, limit, offset],
  );

  const detail = useApiResource<IncidentDetail | null>(
    () => (selectedId ? api.incident(selectedId) : Promise.resolve(null)),
    [selectedId],
  );

  // Success notices are transient; clear them after a moment.
  useEffect(() => {
    if (!actionNotice) return;
    const handle = window.setTimeout(() => setActionNotice(null), 4000);
    return () => window.clearTimeout(handle);
  }, [actionNotice]);

  const page = incidents.data;
  const scenarioList = scenarios.data?.items ?? [];

  // Only trust the loaded detail when it belongs to the current selection, so a
  // previously loaded incident is never displayed under a new selection.
  const detailData = detail.data && detail.data.id === selectedId ? detail.data : null;

  function selectIncident(id: string) {
    setSelectedId(id);
    setNote('');
    setActionError(null);
    setActionNotice(null);
    setReplay(null);
    setReplayError(null);
  }

  async function handleStatus(next: string) {
    if (!selectedId) return;
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await api.updateIncidentStatus(selectedId, next);
      detail.reload();
      setActionNotice(`Status updated to ${next.replace(/_/g, ' ')}.`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleAddNote() {
    const trimmed = note.trim();
    if (!selectedId || !trimmed) return;
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await api.addIncidentNote(selectedId, trimmed);
      setNote('');
      detail.reload();
      setActionNotice('Note added to the investigation.');
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleReplay() {
    if (!selectedId) return;
    setReplayBusy(true);
    setReplayError(null);
    try {
      const result = await api.replayIncident(selectedId);
      setReplay(result);
    } catch (cause) {
      setReplayError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReplayBusy(false);
    }
  }

  return (
    <div className="stack">
      <PageHeader
        title="Incident Investigation"
        description="An incident is a correlated cluster of simulated security events and transactions raised for investigation. Accounts are only flagged for investigation or at elevated simulated risk. Replay reproduces the timeline from stored inputs WITHOUT mutating stored history."
      />

      <Card
        title="Incident queue"
        subtitle="Filter by workflow status and scenario, then open an incident to investigate it."
      >
        <div className="filters">
          <Field label="Status">
            <select
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {titleCase(status)}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Scenario">
            <select
              value={scenarioFilter}
              onChange={(event) => {
                setScenarioFilter(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All scenarios</option>
              {scenarioList.map((scenario) => (
                <option key={scenario.key} value={scenario.key}>
                  {scenario.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Rows per page">
            <select
              value={limit}
              onChange={(event) => {
                setLimit(Number(event.target.value));
                setOffset(0);
              }}
            >
              {LIMIT_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <DataTable<Incident>
          columns={INCIDENT_COLUMNS}
          rows={page?.items ?? []}
          keyFor={(row) => row.id}
          onRowClick={(row) => selectIncident(row.id)}
          loading={incidents.loading}
          error={incidents.error}
          onRetry={incidents.reload}
          emptyTitle="No incidents"
          emptyMessage="No incidents match the current filters."
        />

        {page && (
          <div className="row row--between">
            <span className="dim small">
              Showing {formatNumber(page.returned)} of {formatNumber(page.total)}
            </span>
            <div className="row">
              <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                Previous
              </Button>
              <Button
                size="small"
                disabled={offset + limit >= page.total}
                onClick={() => setOffset(offset + limit)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>

      {selectedId && (
        <Card title="Incident detail" subtitle="Correlated timeline, notes and non-destructive replay.">
          {detail.loading && !detailData && <Loading label="Loading incident…" />}
          {detail.error && !detailData && (
            <ErrorState message={detail.error} onRetry={detail.reload} />
          )}

          {detailData && (
            <>
              <div className="row">
                <strong>{detailData.title}</strong>
                <Badge tone={severityTone(detailData.severity)}>{detailData.severity}</Badge>
                <StatusBadge status={detailData.status} />
              </div>

              <KeyValue
                items={[
                  { label: 'ID', value: <span className="mono">{detailData.id}</span> },
                  {
                    label: 'Scenario',
                    value: detailData.scenario_name ?? titleCase(detailData.scenario),
                  },
                  {
                    label: 'Primary account',
                    value: (
                      <span className="mono">
                        {detailData.primary_account_label ?? detailData.primary_account_id}
                      </span>
                    ),
                  },
                  { label: 'Risk level', value: detailData.risk_level },
                  { label: 'Opened', value: formatDateTime(detailData.opened_at) },
                  {
                    label: 'Closed',
                    value: detailData.closed_at ? formatDateTime(detailData.closed_at) : UNKNOWN,
                  },
                  { label: 'Suspected loss', value: formatMinor(detailData.suspected_loss_minor) },
                  { label: 'Event count', value: formatNumber(detailData.event_count) },
                  { label: 'Transaction count', value: formatNumber(detailData.transaction_count) },
                ]}
              />

              <p className="muted">{detailData.summary}</p>

              <div className="stack">
                <h3>Triggered rules</h3>
                {detailData.triggered_rules.length === 0 ? (
                  <Notice>No correlation rules were triggered for this incident.</Notice>
                ) : (
                  <div className="row">
                    {detailData.triggered_rules.map((rule) => (
                      <Badge key={rule} tone="flag">
                        {rule}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              <div className="stack">
                <h3>Status workflow</h3>
                <div className="row">
                  {STATUSES.map((status) => (
                    <Button
                      key={status}
                      size="small"
                      disabled={busy || detailData.status === status}
                      onClick={() => handleStatus(status)}
                    >
                      {titleCase(status)}
                    </Button>
                  ))}
                </div>
                {actionNotice && <Notice>{actionNotice}</Notice>}
                {actionError && <Notice tone="warning">{actionError}</Notice>}
              </div>

              <div className="stack">
                <h3>Notes</h3>
                <pre className="code">{detailData.notes.trim() || 'No notes recorded yet.'}</pre>
                <Field label="Add a note" hint="Notes are stored against the incident for the investigation record.">
                  <textarea
                    rows={3}
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="Observation, follow-up or containment step…"
                  />
                </Field>
                <div className="row">
                  <Button onClick={handleAddNote} disabled={busy || note.trim().length === 0}>
                    Add note
                  </Button>
                </div>
              </div>

              <div className="stack">
                <h3>Timeline</h3>
                <IncidentTimeline entries={detailData.timeline} />
              </div>

              <div className="stack">
                <h3>Financial impact</h3>
                {detailData.financial_impact ? (
                  <KeyValue
                    items={[
                      { label: 'Exposure', value: formatMinor(detailData.financial_impact.exposure_minor) },
                      {
                        label: 'Unauthorized',
                        value: formatMinor(detailData.financial_impact.unauthorized_minor),
                      },
                      {
                        label: 'Prevented',
                        value: formatMinor(detailData.financial_impact.prevented_minor),
                      },
                      {
                        label: 'Interrupted (legitimate value stopped)',
                        value: formatMinor(detailData.financial_impact.interrupted_minor),
                      },
                      {
                        label: 'Disruption cost',
                        value: formatMinor(detailData.financial_impact.disruption_cost_minor),
                      },
                      {
                        label: 'Recovery cost',
                        value: formatMinor(detailData.financial_impact.recovery_cost_minor),
                      },
                      {
                        label: 'Net impact',
                        value: formatMinor(detailData.financial_impact.net_impact_minor),
                      },
                    ]}
                  />
                ) : (
                  <Notice>No financial impact has been computed for this incident.</Notice>
                )}
              </div>

              <div className="stack">
                <h3>Defense actions</h3>
                {detailData.defense_actions.length === 0 ? (
                  <p className="dim small">No defense actions were taken for this incident.</p>
                ) : (
                  <DataTable<Json>
                    columns={jsonColumns(detailData.defense_actions)}
                    rows={detailData.defense_actions}
                    keyFor={rowKey}
                  />
                )}
              </div>

              <div className="stack">
                <h3>Detections</h3>
                {detailData.detections.length === 0 ? (
                  <p className="dim small">No detection records are attached to this incident.</p>
                ) : (
                  <DataTable<Json>
                    columns={jsonColumns(detailData.detections)}
                    rows={detailData.detections}
                    keyFor={rowKey}
                  />
                )}
              </div>

              <div className="stack">
                <h3>Replay</h3>
                <div className="row">
                  <Button variant="primary" onClick={handleReplay} disabled={replayBusy}>
                    Replay incident
                  </Button>
                  {replayBusy && <Loading label="Replaying…" />}
                </div>
                {replayError && <Notice tone="warning">{replayError}</Notice>}
              </div>
            </>
          )}
        </Card>
      )}

      {replay && (
        <Card title="Replay result" subtitle="Reproduced from stored inputs — the original history is untouched.">
          <KeyValue
            items={[
              { label: 'Replay ID', value: <span className="mono">{replay.replay_id}</span> },
              { label: 'Started', value: formatDateTime(replay.started_at) },
              { label: 'Finished', value: formatDateTime(replay.finished_at) },
              { label: 'Steps replayed', value: formatNumber(replay.steps_replayed) },
              { label: 'Divergences', value: formatNumber(replay.divergence_count) },
            ]}
          />

          <p className="muted small">
            {replay.divergence_count === 0
              ? 'The replay matched the original timeline exactly.'
              : `${formatNumber(replay.divergence_count)} step(s) diverged from the original timeline.`}
          </p>

          <div className="row">
            <span className="muted small">Mutates stored history</span>
            <Badge tone={replay.mutates_history ? 'block' : 'allow'}>
              {replay.mutates_history ? 'yes' : 'no'}
            </Badge>
          </div>
          <Notice tone={replay.mutates_history ? 'warning' : 'info'}>
            This replay is non-destructive: the stored incident history was NOT modified. It only
            reproduces the timeline from the original simulated inputs.
          </Notice>

          {Object.keys(replay.summary).length > 0 && (
            <KeyValue
              items={Object.entries(replay.summary).map(([key, value]) => ({
                label: titleCase(key),
                value: asText(value),
              }))}
            />
          )}

          <div className="stack">
            <h3>Replay steps</h3>
            <DataTable<ReplayStep>
              columns={REPLAY_STEP_COLUMNS}
              rows={replay.steps}
              keyFor={(step) => String(step.seq)}
              emptyTitle="No replay steps"
              emptyMessage="The replay produced no steps."
            />
          </div>
        </Card>
      )}
    </div>
  );
}

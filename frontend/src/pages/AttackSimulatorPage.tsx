/**
 * Module B — Attack Simulator.
 *
 * Launches a controlled attack scenario against the synthetic bank. A run is defined
 * by (scenario, defense configuration, event count, seed). The seed makes a run
 * reproducible: the same four inputs produce the same SHA-256-fingerprinted scenario,
 * which is what lets the Defense Comparison Lab claim a controlled experiment.
 *
 * Nothing here touches a real system — the engine only writes rows to the local
 * SQLite database.
 */
import { useCallback, useMemo, useState } from 'react';

import { Badge, Button, Card, DataTable, Field, Notice, PageHeader, StatCard, StatusBadge, type Column } from '../components/ui';
import { api } from '../api/client';
import type {
  DefenseConfigInfo,
  FinancialImpact,
  ScenarioInfo,
  SimulationRun,
  SimulationRunDetail,
} from '../api/types';
import { useApiResource, useInterval } from '../hooks/useApiResource';
import { formatDateTime, formatMinor, formatNumber, titleCase, UNKNOWN } from '../lib/format';

type Json = Record<string, unknown>;

const asText = (value: unknown): string => {
  if (value === null || value === undefined) return UNKNOWN;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
};

const TERMINAL = new Set(['COMPLETED', 'FAILED', 'STOPPED']);

export function AttackSimulatorPage() {
  const scenarios = useApiResource(() => api.scenarios(), []);
  const configs = useApiResource(() => api.defenseConfigs(), []);
  const [offset, setOffset] = useState(0);
  const runs = useApiResource(() => api.simulationRuns({ limit: 20, offset }), [offset]);

  const [scenarioKey, setScenarioKey] = useState('');
  const [defenseConfig, setDefenseConfig] = useState('COMBINED');
  const [mode, setMode] = useState('batch');
  const [nEvents, setNEvents] = useState(120);
  const [seed, setSeed] = useState(42);
  const [pace, setPace] = useState(12);

  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const runDetail = useApiResource<SimulationRunDetail | null>(
    () => (selectedId ? api.simulationRun(selectedId) : Promise.resolve(null)),
    [selectedId],
  );

  // Poll only while something is actually happening, so an idle dashboard is quiet.
  const activeStatus = runDetail.data?.status;
  const shouldPoll = activeStatus !== undefined && !TERMINAL.has(activeStatus);
  useInterval(useCallback(() => {
    runDetail.reload();
    runs.reload();
  }, [runDetail, runs]), shouldPoll ? 2000 : null);

  const scenarioList: ScenarioInfo[] = scenarios.data?.items ?? [];
  const configList: DefenseConfigInfo[] = configs.data?.items ?? [];
  const effectiveScenario = scenarioKey || scenarioList[0]?.key || '';
  const selectedScenario = scenarioList.find((item) => item.key === effectiveScenario);

  const runColumns: Column<SimulationRun>[] = useMemo(
    () => [
      { key: 'id', header: 'Run', render: (row) => <span className="mono">{row.id}</span> },
      { key: 'scenario', header: 'Scenario', render: (row) => row.scenario_name ?? titleCase(row.scenario) },
      { key: 'defense', header: 'Defense', render: (row) => <Badge tone="info">{row.defense_config}</Badge> },
      { key: 'status', header: 'Status', render: (row) => <StatusBadge status={row.status} /> },
      { key: 'mode', header: 'Mode', render: (row) => titleCase(row.mode) },
      { key: 'events', header: 'Events', numeric: true, render: (row) => `${formatNumber(row.generated_events)} / ${formatNumber(row.requested_events)}` },
      { key: 'seed', header: 'Seed', numeric: true, render: (row) => formatNumber(row.seed) },
      { key: 'created', header: 'Created', render: (row) => formatDateTime(row.created_at) },
    ],
    [],
  );

  async function launch() {
    if (!effectiveScenario) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const created = await api.createSimulationRun({
        scenario: effectiveScenario,
        n_events: nEvents,
        seed,
        defense_config: defenseConfig,
        mode,
        pace_per_second: pace,
      });
      setSelectedId(created.id);
      runs.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function stopSelected() {
    if (!selectedId) return;
    setActionError(null);
    try {
      await api.stopSimulationRun(selectedId);
      runDetail.reload();
      runs.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function resetSelected() {
    if (!selectedId) return;
    setActionError(null);
    try {
      await api.resetSimulationRun(selectedId);
      setSelectedId(null);
      runs.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const detail = runDetail.data;

  return (
    <>
      <PageHeader
        title="Attack Simulator"
        description="Run a controlled attack scenario against the synthetic bank. The same scenario, seed and event count always reproduce the same sequence, which is what makes the experiments repeatable."
        actions={
          <Button variant="primary" onClick={launch} disabled={submitting || !effectiveScenario}>
            {submitting ? 'Launching…' : 'Launch run'}
          </Button>
        }
      />

      {actionError && <Notice tone="warning">{actionError}</Notice>}

      <div className="grid grid--halves">
        <Card title="Run configuration" subtitle="Choose what to exercise and how the bank defends itself">
          <div className="filters">
            <Field label="Attack scenario">
              <select value={effectiveScenario} onChange={(event) => setScenarioKey(event.target.value)}>
                {scenarioList.length === 0 && <option value="">Loading scenarios…</option>}
                {scenarioList.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Defense configuration" hint="NONE = observe only, RULE_BASED = deterministic rules, COMBINED = rules + ML + correlation">
              <select value={defenseConfig} onChange={(event) => setDefenseConfig(event.target.value)}>
                {configList.length === 0 && <option value="COMBINED">COMBINED</option>}
                {configList.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Mode" hint="batch executes immediately; stream paces events so Live Monitoring can follow along">
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                <option value="batch">batch</option>
                <option value="stream">stream</option>
              </select>
            </Field>
            <Field label="Events" hint="Upper bound enforced by the server">
              <input
                type="number"
                min={10}
                max={6000}
                value={nEvents}
                onChange={(event) => setNEvents(Number(event.target.value))}
              />
            </Field>
            <Field label="Seed" hint="Same seed + scenario ⇒ identical run">
              <input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
            </Field>
            {mode === 'stream' && (
              <Field label="Pace (events / second)">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={pace}
                  onChange={(event) => setPace(Number(event.target.value))}
                />
              </Field>
            )}
          </div>
        </Card>

        <Card title="Scenario briefing" subtitle={selectedScenario?.objective ?? 'Select a scenario'}>
          {selectedScenario ? (
            <>
              <p className="muted" style={{ margin: 0 }}>{selectedScenario.description}</p>
              <div className="row">
                <Badge tone="info">{titleCase(selectedScenario.risk_level)} risk</Badge>
                <Badge tone="neutral">{formatNumber(selectedScenario.default_events)} default events</Badge>
              </div>
              <div>
                <h3>Attack signature</h3>
                <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                  {selectedScenario.signature.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <p className="dim small">{scenarios.loading ? 'Loading scenarios…' : 'No scenarios available.'}</p>
          )}
          {scenarios.error && <Notice tone="warning">{scenarios.error}</Notice>}
        </Card>
      </div>

      {configList.length > 0 && (
        <div className="grid grid--thirds">
          {configList.map((config) => (
            <Card key={config.key} title={config.name}>
              <p className="muted small" style={{ margin: 0 }}>{config.description}</p>
              <div className="row">
                <Badge tone={config.uses_ml ? 'info' : 'neutral'}>{config.uses_ml ? 'uses ML' : 'no ML'}</Badge>
                <Badge tone={config.uses_correlation ? 'info' : 'neutral'}>
                  {config.uses_correlation ? 'uses event correlation' : 'no correlation'}
                </Badge>
              </div>
            </Card>
          ))}
        </div>
      )}

      {detail && (
        <Card
          title={`Run ${detail.id}`}
          subtitle={`${detail.scenario_name ?? titleCase(detail.scenario)} · ${detail.defense_config} · seed ${detail.seed}`}
          actions={
            <div className="row">
              <Button size="small" onClick={stopSelected} disabled={TERMINAL.has(detail.status)}>
                Stop
              </Button>
              <Button size="small" variant="danger" onClick={resetSelected}>
                Delete run
              </Button>
            </div>
          }
        >
          <div className="grid grid--stats">
            <StatCard label="Status" value={<StatusBadge status={detail.status} />} />
            <StatCard label="Generated events" value={formatNumber(detail.generated_events)} hint={`of ${formatNumber(detail.requested_events)} requested`} />
            <StatCard label="Incidents raised" value={formatNumber(detail.incidents.length)} tone="flag" />
            <StatCard
              label="Net simulated impact"
              value={detail.financial_impact ? formatMinor(detail.financial_impact.net_impact_minor) : UNKNOWN}
              tone={detail.financial_impact && detail.financial_impact.net_impact_minor < 0 ? 'block' : 'allow'}
              hint="Prevented value minus realized loss and costs"
            />
          </div>

          {detail.error && <Notice tone="warning">Run error: {detail.error}</Notice>}

          {detail.assumptions.length > 0 && (
            <div>
              <h3>Assumptions behind these figures</h3>
              <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                {detail.assumptions.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid grid--halves">
            <div>
              <h3>Detection summary</h3>
              <DataTable<{ key: string; value: string }>
                columns={[
                  { key: 'key', header: 'Metric', render: (row) => <span className="mono">{row.key}</span> },
                  { key: 'value', header: 'Value', numeric: true, render: (row) => row.value },
                ]}
                rows={Object.entries(detail.detection_summary).map(([key, value]) => ({ key, value: asText(value) }))}
                keyFor={(row) => row.key}
                emptyTitle="No detection summary"
                emptyMessage="This run produced no scored transactions."
              />
            </div>
            <div>
              <h3>Financial impact</h3>
              <FinancialImpactPanel impact={detail.financial_impact} />
            </div>
          </div>

          <div>
            <h3>Incidents raised by this run</h3>
            <DataTable<Json>
              columns={jsonColumns(detail.incidents)}
              rows={keyed(detail.incidents)}
              keyFor={(row) => asText(row.__key)}
              emptyTitle="No incidents"
              emptyMessage="This run did not cross the incident-raising threshold."
            />
          </div>
        </Card>
      )}

      <Card title="Simulation runs" subtitle="Select a run to inspect its detections and simulated impact">
        <DataTable<SimulationRun>
          columns={runColumns}
          rows={runs.data?.items ?? []}
          keyFor={(row) => row.id}
          loading={runs.loading}
          error={runs.error}
          onRetry={runs.reload}
          onRowClick={(row) => setSelectedId(row.id)}
          emptyTitle="No runs yet"
          emptyMessage="Configure a scenario above and launch your first run."
        />
        <div className="row row--between">
          <span className="dim small">
            Showing {formatNumber(runs.data?.returned ?? 0)} of {formatNumber(runs.data?.total ?? 0)} runs
          </span>
          <div className="row">
            <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>
              Previous
            </Button>
            <Button
              size="small"
              disabled={(runs.data?.returned ?? 0) + offset >= (runs.data?.total ?? 0)}
              onClick={() => setOffset(offset + 20)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}

function FinancialImpactPanel({ impact }: { impact: FinancialImpact | null }) {
  if (!impact) {
    return (
      <p className="dim small">
        No financial-impact record yet. Impact is computed once a run has produced detections and settlements.
      </p>
    );
  }
  const rows: { label: string; value: string }[] = [
    { label: 'Exposure', value: formatMinor(impact.exposure_minor) },
    { label: 'Unauthorized (settled)', value: formatMinor(impact.unauthorized_minor) },
    { label: 'Prevented', value: formatMinor(impact.prevented_minor) },
    { label: 'Legitimate value interrupted', value: formatMinor(impact.interrupted_minor) },
    { label: 'Disruption cost', value: formatMinor(impact.disruption_cost_minor) },
    { label: 'Recovery cost', value: formatMinor(impact.recovery_cost_minor) },
    { label: 'Net impact', value: formatMinor(impact.net_impact_minor) },
  ];
  return (
    <>
      <DataTable<{ label: string; value: string }>
        columns={[
          { key: 'label', header: 'Line', render: (row) => row.label },
          { key: 'value', header: 'Amount', numeric: true, render: (row) => row.value },
        ]}
        rows={rows}
        keyFor={(row) => row.label}
      />
      {impact.assumptions.length > 0 && (
        <ul className="dim small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
          {impact.assumptions.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function keyed(rows: Json[]): Json[] {
  return rows.map((row, index) => ({ ...row, __key: asText(row.id ?? row.incident_id ?? `row-${index}`) }));
}

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

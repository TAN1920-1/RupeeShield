/**
 * Defense Comparison (Module I).
 *
 * The core research question: does the COMBINED defense (rules + ML + event
 * correlation) outperform RULE_BASED alone and NONE? To answer that fairly the
 * backend runs the SAME scenario, the SAME event stream and the SAME seed against
 * each defense configuration, so the only thing that varies is the strategy.
 *
 * The hypothesis is NOT assumed to be proven. Nothing here claims the combined
 * strategy "wins" — the page only reports measured numbers and surfaces the
 * caveats the backend returns.
 */
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { DefenseComparison, DefenseConfigResult } from '../api/types';
import { BreakdownChart } from '../components/charts';
import {
  Badge,
  Button,
  Card,
  DataTable,
  ErrorState,
  Field,
  KeyValue,
  Loading,
  Notice,
  PageHeader,
} from '../components/ui';
import type { Column } from '../components/ui';
import {
  formatDateTime,
  formatMetric,
  formatMinor,
  formatNumber,
  formatPercent,
  titleCase,
} from '../lib/format';
import { useApiResource } from '../hooks/useApiResource';

const RESULT_COLUMNS: Column<DefenseConfigResult>[] = [
  { key: 'name', header: 'Configuration', render: (row) => row.name },
  {
    key: 'detected',
    header: 'Detected events',
    render: (row) => formatNumber(row.detected_events),
    numeric: true,
  },
  {
    key: 'tp',
    header: 'True positives',
    render: (row) => formatNumber(row.true_positives),
    numeric: true,
  },
  {
    key: 'fp',
    header: 'False positives',
    render: (row) => formatNumber(row.false_positives),
    numeric: true,
  },
  {
    key: 'fn',
    header: 'False negatives',
    render: (row) => formatNumber(row.false_negatives),
    numeric: true,
  },
  {
    key: 'precision',
    header: 'Precision',
    render: (row) => formatMetric(row.precision, 3),
    numeric: true,
  },
  {
    key: 'recall',
    header: 'Recall',
    render: (row) => formatMetric(row.recall, 3),
    numeric: true,
  },
  {
    key: 'fpr',
    header: 'False positive rate',
    render: (row) => formatPercent(row.false_positive_rate),
    numeric: true,
  },
  {
    key: 'latency',
    header: 'Detection latency (ms)',
    render: (row) => formatMetric(row.detection_latency_ms, 2),
    numeric: true,
  },
  {
    key: 'prevented_count',
    header: 'Prevented count',
    render: (row) => formatNumber(row.prevented_count),
    numeric: true,
  },
  {
    key: 'prevented_value',
    header: 'Prevented value',
    render: (row) => formatMinor(row.prevented_value_minor),
    numeric: true,
  },
  {
    key: 'realized_loss',
    header: 'Realized loss',
    render: (row) => formatMinor(row.realized_loss_minor),
    numeric: true,
  },
  {
    key: 'net_impact',
    header: 'Net impact',
    render: (row) => formatMinor(row.net_impact_minor),
    numeric: true,
  },
  {
    key: 'processing',
    header: 'Processing (ms)',
    render: (row) => formatMetric(row.processing_ms, 2),
    numeric: true,
  },
];

const HISTORY_COLUMNS: Column<DefenseComparison>[] = [
  { key: 'id', header: 'ID', render: (row) => <span className="mono">{row.id}</span> },
  {
    key: 'scenario',
    header: 'Scenario',
    render: (row) => row.scenario_name ?? titleCase(row.scenario),
  },
  { key: 'seed', header: 'Seed', render: (row) => formatNumber(row.seed), numeric: true },
  { key: 'n_events', header: 'Events', render: (row) => formatNumber(row.n_events), numeric: true },
  { key: 'created', header: 'Created', render: (row) => formatDateTime(row.created_at) },
];

export function DefenseComparisonPage() {
  const [scenario, setScenario] = useState('');
  const [nEventsInput, setNEventsInput] = useState<number | null>(null);
  const [seed, setSeed] = useState(42);

  const [comparison, setComparison] = useState<DefenseComparison | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const configs = useApiResource(() => api.defenseConfigs(), []);
  const scenarios = useApiResource(() => api.scenarios(), []);
  const history = useApiResource(() => api.defenseComparisons({ limit: 10 }), []);

  const scenarioList = scenarios.data?.items ?? [];
  const selectedScenarioInfo = scenarioList.find((item) => item.key === scenario);
  // Default to the selected scenario's recommended event count until the user
  // types their own value.
  const nEvents = nEventsInput ?? selectedScenarioInfo?.default_events ?? 100;

  // Pick the first scenario once the catalogue has loaded.
  useEffect(() => {
    const items = scenarios.data?.items;
    if (scenario === '' && items && items.length > 0) {
      setScenario(items[0].key);
    }
  }, [scenarios.data, scenario]);

  async function handleRun() {
    if (!scenario) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.compareDefenses({ scenario, n_events: nEvents, seed });
      setComparison(result);
      history.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <PageHeader
        title="Defense Comparison"
        description="This lab runs the SAME scenario, event stream and seed against each defense configuration, so the only thing that differs is the strategy. The hypothesis is not claimed to be proven unless the measured numbers support it — read the conclusion and caveats together."
      />

      <Card
        title="Defense configurations"
        subtitle="Each configuration describes which signal sources the defense is allowed to use."
      >
        {configs.loading && !configs.data && <Loading label="Loading configurations…" />}
        {configs.error && <ErrorState message={configs.error} onRetry={configs.reload} />}
        {configs.data && (
          <div className="grid grid--thirds">
            {configs.data.items.map((config) => (
              <Card key={config.key} title={config.name} subtitle={titleCase(config.key)}>
                <p className="muted small">{config.description}</p>
                <div className="row">
                  <Badge tone={config.uses_ml ? 'info' : 'neutral'}>
                    {config.uses_ml ? 'uses ML' : 'no ML'}
                  </Badge>
                  <Badge tone={config.uses_correlation ? 'info' : 'neutral'}>
                    {config.uses_correlation ? 'uses event correlation' : 'no event correlation'}
                  </Badge>
                </div>
              </Card>
            ))}
          </div>
        )}
      </Card>

      <Card title="Run a controlled comparison">
        {scenarios.loading && !scenarios.data && <Loading label="Loading scenarios…" />}
        {scenarios.error && <ErrorState message={scenarios.error} onRetry={scenarios.reload} />}
        {scenarios.data && (
          <div className="filters">
            <Field label="Scenario">
              <select
                value={scenario}
                onChange={(event) => {
                  setScenario(event.target.value);
                  setNEventsInput(null);
                }}
              >
                <option value="">Select a scenario…</option>
                {scenarioList.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="Number of events"
              hint="Defaults to the scenario's recommended event count."
            >
              <input
                type="number"
                min={1}
                value={nEvents}
                onChange={(event) => setNEventsInput(Math.max(1, Number(event.target.value)))}
              />
            </Field>

            <Field label="Random seed" hint="The same seed is reused for every configuration.">
              <input
                type="number"
                value={seed}
                onChange={(event) => setSeed(Number(event.target.value))}
              />
            </Field>

            <Button variant="primary" onClick={handleRun} disabled={submitting || !scenario}>
              Run comparison
            </Button>
          </div>
        )}

        {submitting && <Loading label="Running comparison…" />}
        {error && <Notice tone="warning">{error}</Notice>}
      </Card>

      {comparison && (
        <>
          <Card title="Comparison result">
            <Notice tone={comparison.same_inputs ? 'info' : 'warning'}>
              Ground-truth labels are synthetic: {comparison.label_source}. Results are measured
              against simulated event labels and do not describe real-world fraud.{' '}
              {comparison.same_inputs
                ? 'All configurations shared identical inputs (same scenario, event stream and seed), so this comparison is controlled.'
                : 'Warning: the configurations did NOT share identical inputs, so this comparison is not controlled.'}
            </Notice>

            <KeyValue
              items={[
                { label: 'Comparison ID', value: <span className="mono">{comparison.id}</span> },
                {
                  label: 'Scenario',
                  value: comparison.scenario_name ?? titleCase(comparison.scenario),
                },
                { label: 'Seed', value: formatNumber(comparison.seed) },
                { label: 'Events', value: formatNumber(comparison.n_events) },
                { label: 'Created', value: formatDateTime(comparison.created_at) },
                { label: 'Label source', value: comparison.label_source },
              ]}
            />

            <DataTable<DefenseConfigResult>
              columns={RESULT_COLUMNS}
              rows={comparison.results}
              keyFor={(row) => row.config}
              emptyTitle="No results"
              emptyMessage="The comparison returned no configuration results."
            />
          </Card>

          <div className="grid grid--halves">
            <Card
              title="Net impact by configuration"
              subtitle="Positive is better. Values are in rupees (major units); the chart's default formatter rounds for display."
            >
              <BreakdownChart
                points={comparison.results.map((row) => ({
                  label: row.name,
                  value: row.net_impact,
                }))}
              />
            </Card>

            <Card
              title="Realized loss by configuration"
              subtitle="Lower is better. Values are in rupees (major units)."
            >
              <BreakdownChart
                points={comparison.results.map((row) => ({
                  label: row.name,
                  value: row.realized_loss,
                }))}
                colours={['#ef4d4d', '#f5b942', '#35c46a']}
              />
            </Card>
          </div>

          <Card title="Conclusion">
            {comparison.conclusion.length === 0 ? (
              <p className="dim small">No conclusion was recorded for this comparison.</p>
            ) : (
              <ul>
                {comparison.conclusion.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Notes / caveats">
            {comparison.notes.length === 0 ? (
              <p className="dim small">No caveats were recorded for this comparison.</p>
            ) : (
              <Notice tone="warning">
                <ul>
                  {comparison.notes.map((note, index) => (
                    <li key={`${index}-${note}`}>{note}</li>
                  ))}
                </ul>
              </Notice>
            )}
          </Card>
        </>
      )}

      <Card
        title="Comparison history"
        subtitle="Recent comparisons. Select a row to load its full result above."
      >
        <DataTable<DefenseComparison>
          columns={HISTORY_COLUMNS}
          rows={history.data?.items ?? []}
          keyFor={(row) => row.id}
          onRowClick={(row) => setComparison(row)}
          loading={history.loading}
          error={history.error}
          onRetry={history.reload}
          emptyTitle="No comparisons yet"
          emptyMessage="Run a comparison above to build up history."
        />
      </Card>
    </div>
  );
}

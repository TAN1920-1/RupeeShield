/**
 * Module C — Live Monitoring.
 *
 * One merged, time-ordered feed of everything the simulation engine writes: security
 * telemetry (logins, device changes, payee additions) and scored transactions. Data
 * arrives over Server-Sent Events, so the page reflects the engine as it runs rather
 * than polling a snapshot.
 *
 * The SSE protocol defines four event types (`message`, `heartbeat`, `complete`,
 * `timeout`); `message` carries one monitoring row, which is the only one this page
 * renders.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { Badge, Button, Card, DataTable, DecisionBadge, Field, Notice, PageHeader, StatCard, type Column } from '../components/ui';
import { api } from '../api/client';
import type { MonitoringEvent, ScenarioInfo, SimulationRun } from '../api/types';
import { useApiResource } from '../hooks/useApiResource';
import { formatMetric, formatMinor, formatNumber, formatTime, severityTone, titleCase, UNKNOWN } from '../lib/format';

const MAX_ROWS = 400;

type StreamState = 'idle' | 'connecting' | 'live' | 'complete' | 'timeout' | 'error';

export function LiveMonitoringPage() {
  const runs = useApiResource(() => api.simulationRuns({ limit: 20 }), []);
  const scenarios = useApiResource(() => api.scenarios(), []);

  const [runId, setRunId] = useState('');
  const [paused, setPaused] = useState(false);
  const [streamState, setStreamState] = useState<StreamState>('idle');
  const [streamError, setStreamError] = useState<string | null>(null);
  const [events, setEvents] = useState<MonitoringEvent[]>([]);

  const [scenarioKey, setScenarioKey] = useState('');
  const [nEvents, setNEvents] = useState(150);
  const [seed, setSeed] = useState(42);
  const [pace, setPace] = useState(8);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const seen = useRef<Set<number>>(new Set());

  const pushEvents = useCallback((incoming: MonitoringEvent[]) => {
    const fresh = incoming.filter((event) => !seen.current.has(event.sequence));
    if (fresh.length === 0) return;
    fresh.forEach((event) => seen.current.add(event.sequence));
    setEvents((current) =>
      [...fresh, ...current].sort((left, right) => right.sequence - left.sequence).slice(0, MAX_ROWS),
    );
  }, []);

  const clearFeed = useCallback(() => {
    seen.current = new Set();
    setEvents([]);
  }, []);

  const runList: SimulationRun[] = runs.data?.items ?? [];
  const scenarioList: ScenarioInfo[] = scenarios.data?.items ?? [];
  const effectiveScenario = scenarioKey || scenarioList[0]?.key || '';
  const selectedRun = runList.find((run) => run.id === runId);

  // Backfill history once per selected run, then let the stream append to it.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    clearFeed();
    api
      .monitoringEvents({ run_id: runId, limit: 200 })
      .then((page) => {
        if (cancelled) return;
        pushEvents(page.events);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setStreamError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, pushEvents, clearFeed]);

  useEffect(() => {
    if (!runId || paused) {
      if (paused) setStreamState('idle');
      return;
    }
    setStreamState('connecting');
    setStreamError(null);
    let finished = false;
    const source = new EventSource(api.monitoringStreamUrl(runId));

    source.onopen = () => setStreamState('live');
    source.onmessage = (message: MessageEvent<string>) => {
      setStreamState('live');
      try {
        pushEvents([JSON.parse(message.data) as MonitoringEvent]);
      } catch {
        setStreamError('Received an event that could not be parsed.');
      }
    };

    const onComplete = (event: Event) => {
      finished = true;
      let label = 'complete';
      try {
        const payload = JSON.parse((event as MessageEvent<string>).data) as { status?: string };
        if (payload.status) label = `complete (${payload.status})`;
      } catch {
        /* keep the plain label */
      }
      setStreamState('complete');
      setStreamError(`The run finished — ${label}. Stored events remain listed below.`);
      source.close();
      runs.reload();
    };

    const onTimeout = (event: Event) => {
      finished = true;
      let detail = 'The stream was closed by the server to free the connection.';
      try {
        const payload = JSON.parse((event as MessageEvent<string>).data) as { detail?: string };
        if (payload.detail) detail = payload.detail;
      } catch {
        /* keep the default detail */
      }
      setStreamState('timeout');
      setStreamError(detail);
      source.close();
    };

    source.addEventListener('complete', onComplete);
    source.addEventListener('timeout', onTimeout);
    source.onerror = () => {
      if (finished) return;
      // EventSource retries by itself; the browser will report `open` again.
      setStreamState('error');
      setStreamError('The live stream dropped. The browser is retrying automatically.');
    };

    return () => {
      source.removeEventListener('complete', onComplete);
      source.removeEventListener('timeout', onTimeout);
      source.close();
    };
  }, [runId, paused, pushEvents, runs]);

  async function launchStream() {
    if (!effectiveScenario) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const created = await api.createSimulationRun({
        scenario: effectiveScenario,
        n_events: nEvents,
        seed,
        defense_config: 'COMBINED',
        mode: 'stream',
        pace_per_second: pace,
      });
      setRunId(created.id);
      setPaused(false);
      runs.reload();
    } catch (cause) {
      setLaunchError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLaunching(false);
    }
  }

  const blocked = events.filter((event) => event.decision === 'BLOCK').length;
  const flagged = events.filter((event) => event.decision === 'FLAG').length;

  const columns: Column<MonitoringEvent>[] = [
    { key: 'sequence', header: '#', numeric: true, render: (row) => formatNumber(row.sequence) },
    { key: 'time', header: 'Time', render: (row) => formatTime(row.timestamp) },
    {
      key: 'type',
      header: 'Event',
      render: (row) => <Badge tone={severityTone(severityForEvent(row.event_type))}>{titleCase(row.event_type)}</Badge>,
    },
    {
      key: 'account',
      header: 'Account',
      render: (row) => (
        <span className="mono small">{row.account_label ? `${row.account_label} · ${row.account_id}` : row.account_id}</span>
      ),
    },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      render: (row) => (row.amount_minor === null ? UNKNOWN : formatMinor(row.amount_minor)),
    },
    { key: 'risk', header: 'Risk', numeric: true, render: (row) => formatMetric(row.risk_score, 3) },
    { key: 'decision', header: 'Decision', render: (row) => <DecisionBadge decision={row.decision} /> },
    { key: 'response', header: 'Response', render: (row) => <span className="mono small">{row.response}</span> },
    { key: 'detail', header: 'Detail', render: (row) => <span className="small">{row.detail}</span> },
    { key: 'source', header: 'Source', render: (row) => <span className="dim small">{row.source}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Live Monitoring"
        description="A single time-ordered feed of the security telemetry and scored transactions the simulation engine produces for one run, delivered over Server-Sent Events."
        actions={
          <div className="row">
            <Button size="small" onClick={() => setPaused((value) => !value)} disabled={!runId}>
              {paused ? 'Resume stream' : 'Pause stream'}
            </Button>
            <Button size="small" onClick={clearFeed} disabled={events.length === 0}>
              Clear feed
            </Button>
          </div>
        }
      />

      {(streamError || launchError) && <Notice tone="warning">{launchError ?? streamError}</Notice>}

      <Card title="Stream controls" subtitle="Watch an existing run, or start a new paced run to watch from the beginning">
        <div className="filters">
          <Field label="Simulation run" hint="Newest runs first">
            <select value={runId} onChange={(event) => setRunId(event.target.value)}>
              <option value="">Select a run…</option>
              {runList.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.id} · {run.scenario_name ?? titleCase(run.scenario)} · {run.status}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Scenario for a new live run">
            <select value={effectiveScenario} onChange={(event) => setScenarioKey(event.target.value)}>
              {scenarioList.length === 0 && <option value="">Loading…</option>}
              {scenarioList.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Events">
            <input type="number" min={10} max={1500} value={nEvents} onChange={(event) => setNEvents(Number(event.target.value))} />
          </Field>
          <Field label="Seed">
            <input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
          </Field>
          <Field label="Pace (events / second)">
            <input type="number" min={1} max={60} value={pace} onChange={(event) => setPace(Number(event.target.value))} />
          </Field>
          <Button variant="primary" onClick={launchStream} disabled={launching || !effectiveScenario}>
            {launching ? 'Starting…' : 'Start streaming run'}
          </Button>
        </div>

        <div className="row">
          <span>Stream</span>
          <Badge tone={streamTone(streamState)}>{streamState}</Badge>
          {selectedRun && (
            <>
              <span className="dim small">run {selectedRun.id}</span>
              <Badge tone="neutral">{selectedRun.status}</Badge>
              <span className="dim small">
                {formatNumber(selectedRun.generated_events)} / {formatNumber(selectedRun.requested_events)} events generated
              </span>
            </>
          )}
        </div>

        {!runId && (
          <p className="dim small">
            No run selected. Pick one above, or start a paced run — the stream yields events as they are recorded and
            sends a `complete` event when the run reaches a terminal status.
          </p>
        )}
      </Card>

      <div className="grid grid--stats">
        <StatCard label="Events shown" value={formatNumber(events.length)} hint={`Feed capped at ${MAX_ROWS} rows`} />
        <StatCard label="Flagged" value={formatNumber(flagged)} tone="flag" />
        <StatCard label="Blocked" value={formatNumber(blocked)} tone="block" />
        <StatCard
          label="Latest sequence"
          value={events.length > 0 ? formatNumber(events[0].sequence) : UNKNOWN}
          tone="info"
        />
      </div>

      <Card title="Event feed" subtitle="Newest first. Security telemetry and scored transactions are interleaved by the server.">
        <DataTable<MonitoringEvent>
          columns={columns}
          rows={events}
          keyFor={(row) => String(row.sequence)}
          loading={streamState === 'connecting' && events.length === 0}
          error={null}
          emptyTitle="Waiting for events"
          emptyMessage="Nothing has been recorded for this run yet. If the run has finished, select another run or start a new one."
        />
      </Card>
    </>
  );
}

/** Map an engine event type onto the badge vocabulary; unknown types stay neutral. */
function severityForEvent(eventType: string): string | null {
  const upper = eventType.toUpperCase();
  if (upper.includes('FAILED_LOGIN') || upper.includes('TAKEOVER') || upper.includes('BLOCK')) return 'HIGH';
  if (upper.includes('NEW_DEVICE') || upper.includes('NEW_PAYEE') || upper.includes('PASSWORD')) return 'MEDIUM';
  return null;
}

function streamTone(state: StreamState) {
  switch (state) {
    case 'live':
      return 'allow' as const;
    case 'complete':
      return 'info' as const;
    case 'error':
    case 'timeout':
      return 'block' as const;
    case 'connecting':
      return 'flag' as const;
    default:
      return 'neutral' as const;
  }
}

/**
 * Module K — Settings & Documentation.
 *
 * Three tabs: the runtime settings plus a threshold editor that only sends changed
 * keys, the generated documentation payload, and the append-only audit trail.
 */
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { AppSettings, AuditEntry, DocumentationPayload, Paginated, Thresholds } from '../api/types';
import { Badge, Button, Card, DataTable, EmptyState, ErrorState, Field, KeyValue, Loading, Notice, PageHeader, Tabs } from '../components/ui';
import type { Column } from '../components/ui';
import { formatDateTime, formatMinor, formatNumber, titleCase, UNKNOWN } from '../lib/format';
import { useApiResource } from '../hooks/useApiResource';
import type { ApiResource } from '../hooks/useApiResource';

type TabKey = 'settings' | 'docs' | 'audit';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'settings', label: 'Settings' },
  { key: 'docs', label: 'Documentation' },
  { key: 'audit', label: 'Audit Trail' },
];

const asText = (value: unknown): string =>
  value === null || value === undefined
    ? UNKNOWN
    : typeof value === 'string'
      ? value
      : typeof value === 'number' || typeof value === 'boolean'
        ? String(value)
        : JSON.stringify(value);

const THRESHOLD_KEYS: (keyof Thresholds)[] = [
  'block_threshold',
  'flag_threshold',
  'rule_velocity_10min',
  'rule_velocity_1h',
  'rule_amount_zscore',
  'rule_amount_over_avg',
  'failed_login_burst',
  'failed_login_window_minutes',
  'correlation_lookback_minutes',
  'new_device_high_value_minor',
  'coordinated_fanout_beneficiaries',
];

const THRESHOLD_LABELS: Record<keyof Thresholds, string> = {
  block_threshold: 'Block threshold',
  flag_threshold: 'Flag threshold',
  rule_velocity_10min: 'Velocity: transactions / 10 min',
  rule_velocity_1h: 'Velocity: transactions / hour',
  rule_amount_zscore: 'Amount z-score trigger',
  rule_amount_over_avg: 'Amount over account average',
  failed_login_burst: 'Failed-login burst size',
  failed_login_window_minutes: 'Failed-login window (minutes)',
  correlation_lookback_minutes: 'Correlation lookback (minutes)',
  new_device_high_value_minor: 'New-device high value (paise)',
  coordinated_fanout_beneficiaries: 'Coordinated fan-out beneficiaries',
};

type Troubleshooting = { problem: string; resolution: string };

const TROUBLESHOOTING_COLUMNS: Column<Troubleshooting>[] = [
  { key: 'problem', header: 'Problem', render: (row) => row.problem },
  { key: 'resolution', header: 'Resolution', render: (row) => row.resolution },
];

function SettingsTab({ resource }: { resource: ApiResource<AppSettings> }) {
  const { data, loading, error, reload } = resource;
  const [form, setForm] = useState<Record<string, string>>({});
  const [save, setSave] = useState<{ tone: 'info' | 'warning'; message: string; values?: Record<string, number> } | null>(
    null,
  );

  useEffect(() => {
    if (!data) return;
    const seeded: Record<string, string> = {};
    THRESHOLD_KEYS.forEach((key) => {
      seeded[key] = String(data.thresholds[key]);
    });
    setForm(seeded);
  }, [data]);

  if (loading && !data) return <Loading label="Loading settings…" />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) {
    return <EmptyState title="No settings" message="The API returned no settings payload." />;
  }

  const current = data;

  const values = (): Record<string, string> => {
    const seeded: Record<string, string> = {};
    THRESHOLD_KEYS.forEach((key) => {
      seeded[key] = form[key] ?? String(current.thresholds[key]);
    });
    return seeded;
  };

  const resetForm = () => {
    const seeded: Record<string, string> = {};
    THRESHOLD_KEYS.forEach((key) => {
      seeded[key] = String(current.thresholds[key]);
    });
    setForm(seeded);
    setSave(null);
  };

  const submit = async () => {
    const valuesSnapshot = values();
    const changed: Partial<Thresholds> = {};
    THRESHOLD_KEYS.forEach((key) => {
      const next = Number(valuesSnapshot[key]);
      if (Number.isFinite(next) && next !== current.thresholds[key]) {
        changed[key] = next;
      }
    });

    if (Object.keys(changed).length === 0) {
      setSave({ tone: 'info', message: 'No threshold changes to save.' });
      return;
    }

    const sent: Record<string, number> = {};
    Object.entries(changed).forEach(([key, value]) => {
      if (value !== undefined) sent[key] = value;
    });

    try {
      await api.patchSettings({ thresholds: changed });
      setSave({
        tone: 'info',
        message: `Saved ${Object.keys(sent).length} threshold change(s).`,
        values: sent,
      });
      reload();
    } catch (cause) {
      setSave({ tone: 'warning', message: cause instanceof Error ? cause.message : String(cause) });
    }
  };

  return (
    <div className="stack">
      <Card title="Runtime settings" subtitle="Values exposed by /api/settings.">
        <KeyValue
          items={[
            { label: 'Application', value: current.app_name },
            { label: 'Version', value: current.app_version },
            { label: 'Environment', value: current.environment },
            { label: 'Database backend', value: current.database_backend },
            { label: 'Model directory', value: <span className="mono">{current.model_dir}</span> },
            { label: 'Data directory', value: <span className="mono">{current.data_dir}</span> },
            { label: 'Default seed', value: formatNumber(current.default_seed) },
            { label: 'Max simulation events', value: formatNumber(current.max_simulation_events) },
            { label: 'Rate limit (requests / min)', value: formatNumber(current.rate_limit_requests_per_minute) },
            {
              label: 'API training enabled',
              value: (
                <Badge tone={current.enable_api_training ? 'allow' : 'neutral'}>
                  {current.enable_api_training ? 'enabled' : 'disabled'}
                </Badge>
              ),
            },
            {
              label: 'CORS origins',
              value: current.cors_origins.length > 0 ? current.cors_origins.join(', ') : UNKNOWN,
            },
          ]}
        />
      </Card>

      <Card
        title="Threshold tuning"
        subtitle="Only changed thresholds are sent to the API."
        actions={
          <Button size="small" onClick={resetForm}>
            Reset form
          </Button>
        }
      >
        <div className="grid grid--thirds">
          {THRESHOLD_KEYS.map((key) => (
            <Field
              key={key}
              label={THRESHOLD_LABELS[key]}
              hint={
                key === 'new_device_high_value_minor'
                  ? `Stored in minor units (paise): 40000 = ${formatMinor(40000)}`
                  : undefined
              }
            >
              <input
                type="number"
                step="any"
                value={form[key] ?? String(current.thresholds[key])}
                onChange={(event) => setForm((previous) => ({ ...previous, [key]: event.target.value }))}
              />
            </Field>
          ))}
        </div>
        <div className="row">
          <Button variant="primary" onClick={submit}>
            Save thresholds
          </Button>
        </div>
        {save && (
          <Notice tone={save.tone}>
            {save.message}
            {save.values && (
              <KeyValue
                items={Object.entries(save.values).map(([key, value]) => ({
                  label: titleCase(key),
                  value: formatNumber(value),
                }))}
              />
            )}
          </Notice>
        )}
      </Card>

      <Card title="Secret handling">
        <Notice>
          {current.secret_notes.length === 0 ? (
            'No secret-handling notes were reported.'
          ) : (
            <ul className="small">
              {current.secret_notes.map((note, index) => (
                <li key={`${index}-${note}`}>{note}</li>
              ))}
            </ul>
          )}
        </Notice>
      </Card>
    </div>
  );
}

function DocsTab({ resource }: { resource: ApiResource<DocumentationPayload> }) {
  const { data, loading, error, reload } = resource;

  if (loading && !data) return <Loading label="Loading documentation…" />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) {
    return <EmptyState title="No documentation" message="The API returned no documentation payload." />;
  }

  const provenanceEntries = Object.entries(data.dataset_provenance);

  return (
    <div className="stack">
      <Card title={data.title} subtitle={`Version ${data.version}`}>
        <p className="muted small">
          Generated reference for the running build. Formulas and provenance describe the synthetic
          dataset only.
        </p>
      </Card>

      {data.sections.map((section) => (
        <Card key={section.key} title={section.title} subtitle={section.key}>
          <p className="muted small">{section.body}</p>
          {section.bullets.length > 0 && (
            <ul className="small">
              {section.bullets.map((bullet, index) => (
                <li key={`${index}-${bullet}`}>{bullet}</li>
              ))}
            </ul>
          )}
        </Card>
      ))}

      <Card title="Formulas">
        {data.formulas.length === 0 ? (
          <Notice>No formulas documented.</Notice>
        ) : (
          <div className="stack">
            {data.formulas.map((formula, index) => (
              <pre className="code" key={index}>
                {JSON.stringify(formula, null, 2)}
              </pre>
            ))}
          </div>
        )}
      </Card>

      <Card title="Dataset provenance">
        {provenanceEntries.length === 0 ? (
          <Notice>No dataset provenance was reported.</Notice>
        ) : (
          <KeyValue
            items={provenanceEntries.map(([key, value]) => ({ label: titleCase(key), value: asText(value) }))}
          />
        )}
      </Card>

      <Card title="Ethics and responsible use">
        {data.ethics.length === 0 ? (
          <Notice tone="warning">No ethics notes were reported.</Notice>
        ) : (
          <Notice tone="warning">
            <ul className="small">
              {data.ethics.map((item, index) => (
                <li key={`${index}-${item}`}>{item}</li>
              ))}
            </ul>
          </Notice>
        )}
      </Card>

      <Card title="Troubleshooting">
        <DataTable
          columns={TROUBLESHOOTING_COLUMNS}
          rows={data.troubleshooting}
          keyFor={(row) => row.problem}
          emptyTitle="No troubleshooting notes"
          emptyMessage="Nothing recorded."
        />
      </Card>

      <Card title="Commands">
        {data.commands.length === 0 ? (
          <Notice>No commands documented.</Notice>
        ) : (
          <div className="stack">
            {data.commands.map((command, index) => (
              <div className="stack" key={`${command.label}-${index}`}>
                <span className="small muted">{command.label}</span>
                <pre className="code">{command.command}</pre>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function AuditTab({
  resource,
  actionInput,
  onActionInputChange,
  onApply,
  limit,
  onLimitChange,
  offset,
  onOffsetChange,
}: {
  resource: ApiResource<Paginated<AuditEntry>>;
  actionInput: string;
  onActionInputChange: (value: string) => void;
  onApply: () => void;
  limit: number;
  onLimitChange: (value: number) => void;
  offset: number;
  onOffsetChange: (value: number) => void;
}) {
  const { data, loading, error, reload } = resource;
  const entries = data?.items ?? [];

  const columns: Column<AuditEntry>[] = [
    { key: 'timestamp', header: 'Timestamp', render: (row) => formatDateTime(row.timestamp) },
    { key: 'actor', header: 'Actor', render: (row) => row.actor },
    { key: 'action', header: 'Action', render: (row) => <span className="mono">{row.action}</span> },
    { key: 'entity_type', header: 'Entity type', render: (row) => row.entity_type },
    {
      key: 'entity_id',
      header: 'Entity id',
      render: (row) => <span className="mono">{row.entity_id ?? UNKNOWN}</span>,
    },
    {
      key: 'outcome',
      header: 'Outcome',
      render: (row) => <Badge tone={row.outcome === 'SUCCESS' ? 'allow' : 'block'}>{row.outcome}</Badge>,
    },
    {
      key: 'details',
      header: 'Details',
      render: (row) => <span className="mono small">{asText(row.details)}</span>,
    },
  ];

  return (
    <Card title="Audit trail" subtitle="Append-only: entries are never edited or deleted.">
      <div className="filters">
        <Field label="Action filter">
          <input
            type="text"
            value={actionInput}
            placeholder="e.g. SETTINGS_PATCH"
            onChange={(event) => onActionInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onApply();
            }}
          />
        </Field>
        <Field label="Page size">
          <select value={limit} onChange={(event) => onLimitChange(Number(event.target.value))}>
            {[25, 50, 100].map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </Field>
        <Button onClick={onApply}>Apply filter</Button>
      </div>

      <DataTable<AuditEntry>
        columns={columns}
        rows={entries}
        keyFor={(row) => row.id}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyTitle="No audit entries"
        emptyMessage="No entries match the current filter."
      />

      <div className="row row--between">
        <span className="dim small">
          {data ? `Showing ${data.returned} of ${data.total} entries` : 'Loading…'}
        </span>
        <div className="row">
          <Button size="small" onClick={() => onOffsetChange(Math.max(0, offset - limit))} disabled={offset === 0}>
            Previous
          </Button>
          <Button
            size="small"
            onClick={() => onOffsetChange(offset + limit)}
            disabled={!data || offset + limit >= data.total}
          >
            Next
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function SettingsDocsPage() {
  const [tab, setTab] = useState<TabKey>('settings');

  const settings = useApiResource(() => api.settings(), []);
  const docs = useApiResource(() => api.documentation(), []);

  const [actionInput, setActionInput] = useState('');
  const [action, setAction] = useState('');
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);
  const audit = useApiResource(() => api.auditLog({ limit, offset, action }), [limit, offset, action]);

  return (
    <div className="stack">
      <PageHeader
        title="Settings & Documentation"
        description="Runtime settings and threshold tuning, the generated documentation payload, and the append-only audit trail of every state change."
      />

      <Tabs tabs={TABS} active={tab} onChange={(key) => setTab(key as TabKey)} />

      {tab === 'settings' && <SettingsTab resource={settings} />}
      {tab === 'docs' && <DocsTab resource={docs} />}
      {tab === 'audit' && (
        <AuditTab
          resource={audit}
          actionInput={actionInput}
          onActionInputChange={setActionInput}
          onApply={() => {
            setAction(actionInput);
            setOffset(0);
          }}
          limit={limit}
          onLimitChange={(value) => {
            setLimit(value);
            setOffset(0);
          }}
          offset={offset}
          onOffsetChange={setOffset}
        />
      )}
    </div>
  );
}

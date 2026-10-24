/**
 * Module J — ML Model Evaluation.
 *
 * Shows the hybrid detection engine: a supervised model (the probability of the
 * synthetic fraud label), an unsupervised anomaly model (how far a transaction
 * deviates from the account's own history) and deterministic rules, fused by the
 * risk engine.
 *
 * Every number here is measured on a synthetic held-out split. The page never
 * claims general accuracy, and an undefined metric renders as `—` rather than 0.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';

import { api } from '../api/client';
import type { AppSettings, MetricBlock, ModelEvaluation, ModelInfo } from '../api/types';
import { Badge, Button, Card, DataTable, EmptyState, ErrorState, Field, KeyValue, Loading, Notice, PageHeader, StatCard } from '../components/ui';
import type { Column } from '../components/ui';
import { BreakdownChart, PrCurveChart } from '../components/charts';
import type { Point } from '../components/charts';
import { formatDateTime, formatMetric, formatNumber, formatPercent, featureLabel, titleCase, UNKNOWN } from '../lib/format';
import { useApiResource } from '../hooks/useApiResource';
import type { ApiResource } from '../hooks/useApiResource';

const asText = (value: unknown): string =>
  value === null || value === undefined
    ? UNKNOWN
    : typeof value === 'string'
      ? value
      : typeof value === 'number' || typeof value === 'boolean'
        ? String(value)
        : JSON.stringify(value);

const METRIC_COLUMNS: Column<MetricBlock>[] = [
  { key: 'label', header: 'Block', render: (row) => row.label },
  { key: 'split', header: 'Split', render: (row) => row.split },
  { key: 'samples', header: 'Samples', numeric: true, render: (row) => formatNumber(row.samples) },
  { key: 'threshold', header: 'Threshold', numeric: true, render: (row) => formatMetric(row.threshold, 4) },
  { key: 'precision', header: 'Precision', numeric: true, render: (row) => formatMetric(row.precision, 4) },
  { key: 'recall', header: 'Recall', numeric: true, render: (row) => formatMetric(row.recall, 4) },
  { key: 'f1', header: 'F1', numeric: true, render: (row) => formatMetric(row.f1, 4) },
  { key: 'pr_auc', header: 'PR-AUC', numeric: true, render: (row) => formatMetric(row.pr_auc, 4) },
  { key: 'roc_auc', header: 'ROC-AUC', numeric: true, render: (row) => formatMetric(row.roc_auc, 4) },
  { key: 'fpr', header: 'False positive rate', numeric: true, render: (row) => formatPercent(row.false_positive_rate) },
  { key: 'accuracy', header: 'Accuracy', numeric: true, render: (row) => formatMetric(row.accuracy, 4) },
];

const ARTIFACT_COLUMNS: Column<ModelInfo['artifacts'][number]>[] = [
  { key: 'name', header: 'Artefact', render: (row) => row.name },
  { key: 'path', header: 'Path', render: (row) => <span className="mono">{row.path}</span> },
  {
    key: 'exists',
    header: 'Exists',
    render: (row) => <Badge tone={row.exists ? 'allow' : 'block'}>{row.exists ? 'yes' : 'no'}</Badge>,
  },
  {
    key: 'size',
    header: 'Size',
    numeric: true,
    render: (row) => (row.size_bytes === null ? UNKNOWN : `${formatNumber(row.size_bytes)} bytes`),
  },
];

function ArtifactsCard({ artifacts }: { artifacts: ModelInfo['artifacts'] }) {
  return (
    <Card title="Model artefacts" subtitle="/ml/model-info — files the API expects on disk">
      <DataTable
        columns={ARTIFACT_COLUMNS}
        rows={artifacts}
        keyFor={(row) => row.path}
        emptyTitle="No artefacts reported"
        emptyMessage="The API reported no model artefacts."
      />
    </Card>
  );
}

function NotesCard({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <Card title="Model notes">
      <ul className="small">
        {notes.map((note, index) => (
          <li key={`${index}-${note}`}>{note}</li>
        ))}
      </ul>
    </Card>
  );
}

function UnavailablePanel({ info, reason }: { info: ModelInfo; reason: string }) {
  return (
    <div className="stack">
      <Notice tone="warning">
        {reason} The supervised metric charts are hidden until trained artefacts exist, so the
        dashboard never presents placeholder numbers.
      </Notice>
      <Card title="How to train the model" subtitle="Run these commands from the backend project root.">
        {info.setup_instructions.length === 0 ? (
          <Notice>No setup instructions were reported.</Notice>
        ) : (
          <pre className="code">{info.setup_instructions.join('\n')}</pre>
        )}
      </Card>
      <ArtifactsCard artifacts={info.artifacts} />
      <NotesCard notes={info.notes} />
    </div>
  );
}

function ConfusionBlock({
  label,
  labels,
  confusion,
  classBalance,
}: {
  label: string;
  labels: string[][];
  confusion: NonNullable<MetricBlock['confusion']>;
  classBalance: Record<string, number> | null;
}) {
  const items = [
    { label: 'True positive', value: formatNumber(confusion.true_positive) },
    { label: 'False positive', value: formatNumber(confusion.false_positive) },
    { label: 'False negative', value: formatNumber(confusion.false_negative) },
    { label: 'True negative', value: formatNumber(confusion.true_negative) },
    ...Object.entries(classBalance ?? {}).map(([key, value]) => ({
      label: `Class balance · ${titleCase(key)}`,
      value: formatNumber(value),
    })),
  ];
  return (
    <div className="stack">
      <h3>{label}</h3>
      {labels.length > 0 && <p className="dim small">{labels.map((row) => row.join(' / ')).join(' · ')}</p>}
      <KeyValue items={items} />
    </div>
  );
}

function ModelMetrics({
  info,
  evaluation,
  importance,
}: {
  info: ModelInfo;
  evaluation: ModelEvaluation;
  importance: Point[];
}) {
  const primary = evaluation.metrics[0] ?? null;
  const confusionBlocks = evaluation.metrics.filter(
    (block): block is MetricBlock & { confusion: NonNullable<MetricBlock['confusion']> } =>
      block.confusion !== null,
  );
  const calibrationEntries = Object.entries(info.calibration);

  return (
    <div className="stack">
      <div className="grid grid--stats">
        <StatCard label="Model version" value={info.version ?? UNKNOWN} />
        <StatCard label="Trained at" value={formatDateTime(info.trained_at)} />
        <StatCard
          label="SHAP explanations"
          value={<Badge tone={info.shap_available ? 'allow' : 'block'}>{info.shap_available ? 'available' : 'unavailable'}</Badge>}
          hint="Per-transaction feature attributions"
        />
        <StatCard label="Metric blocks" value={formatNumber(evaluation.metrics.length)} />
        <StatCard
          label="Primary PR-AUC"
          value={formatMetric(primary?.pr_auc ?? null, 4)}
          hint={primary ? primary.label : 'No metric blocks reported'}
        />
      </div>

      <ArtifactsCard artifacts={info.artifacts} />

      <Card title="Calibration" subtitle="Platt/isotonic style calibration figures reported by the backend.">
        {calibrationEntries.length === 0 ? (
          <Notice>No calibration figures were reported.</Notice>
        ) : (
          <KeyValue
            items={calibrationEntries.map(([key, value]) => ({ label: titleCase(key), value: formatMetric(value, 4) }))}
          />
        )}
      </Card>

      <Card
        title="Held-out metrics"
        subtitle="Measured on the synthetic held-out split only — not a claim of real-world accuracy."
      >
        <DataTable
          columns={METRIC_COLUMNS}
          rows={evaluation.metrics}
          keyFor={(row) => row.key}
          emptyTitle="No metric blocks"
          emptyMessage="The evaluation report contained no metric blocks."
        />
      </Card>

      <Card title="Precision–recall curves" subtitle="Computed on the synthetic test split.">
        {Object.keys(evaluation.pr_curves).length === 0 ? (
          <Notice>No precision–recall curves were reported.</Notice>
        ) : (
          <div className="grid grid--halves">
            {Object.entries(evaluation.pr_curves).map(([key, curve]) => (
              <Card key={key} title={key}>
                <PrCurveChart
                  recall={curve.recall}
                  precision={curve.precision}
                  averagePrecision={curve.average_precision}
                />
              </Card>
            ))}
          </div>
        )}
      </Card>

      <Card title="Feature importance" subtitle="Top 12 drivers, ranked by importance on the synthetic training data.">
        <BreakdownChart points={importance} horizontal valueFormatter={(value) => formatMetric(value, 4)} />
      </Card>

      <Card title="Confusion matrices" subtitle="Counts at the operating threshold for each block that reported one.">
        {confusionBlocks.length === 0 ? (
          <Notice>No confusion matrices were reported.</Notice>
        ) : (
          <div className="stack">
            {confusionBlocks.map((block) => (
              <ConfusionBlock
                key={block.key}
                label={block.label}
                labels={block.confusion.labels}
                confusion={block.confusion}
                classBalance={block.class_balance}
              />
            ))}
          </div>
        )}
      </Card>

      <Card title="Limitations">
        {evaluation.limitations.length === 0 ? (
          <Notice>No limitations were recorded.</Notice>
        ) : (
          <Notice tone="warning">
            <ul className="small">
              {evaluation.limitations.map((limitation, index) => (
                <li key={`${index}-${limitation}`}>{limitation}</li>
              ))}
            </ul>
          </Notice>
        )}
      </Card>

      <Card title="Evaluation provenance">
        <KeyValue
          items={[
            { label: 'Split strategy', value: evaluation.split_strategy },
            { label: 'Generated at', value: formatDateTime(evaluation.generated_at) },
            ...Object.entries(evaluation.dataset).map(([key, value]) => ({
              label: titleCase(key),
              value: asText(value),
            })),
            ...Object.entries(evaluation.latency).map(([key, value]) => ({
              label: `${titleCase(key)} (ms)`,
              value: formatMetric(value, 4),
            })),
          ]}
        />
      </Card>
    </div>
  );
}

function TrainingCard({ settings, onTrained }: { settings: ApiResource<AppSettings>; onTrained: () => void }) {
  const [nUsers, setNUsers] = useState(1000);
  const [nTransactions, setNTransactions] = useState(20000);
  const [fraudRate, setFraudRate] = useState(0.02);
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    status: string;
    message: string;
    regenerate_dataset: boolean;
    metrics: Record<string, unknown>;
  } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  if (settings.loading && !settings.data) {
    return (
      <Card title="Retrain model">
        <Loading label="Loading training settings…" />
      </Card>
    );
  }

  if (settings.error && !settings.data) {
    return (
      <Card title="Retrain model">
        <ErrorState message={settings.error} onRetry={settings.reload} />
      </Card>
    );
  }

  if (!settings.data) {
    return (
      <Card title="Retrain model">
        <EmptyState title="Training unavailable" message="Training settings could not be loaded." />
      </Card>
    );
  }

  if (!settings.data.enable_api_training) {
    return (
      <Card title="Retrain model" subtitle="/ml/train">
        <Notice tone="warning">
          API training is disabled by default (<code className="mono">enable_api_training = false</code>).
          Train offline with <code className="mono">python -m app.ml.train</code> from the backend project
          root, then reload this page.
        </Notice>
        <div className="row">
          <Button disabled>Retrain model</Button>
        </div>
      </Card>
    );
  }

  const submit = async () => {
    setBusy(true);
    setFailure(null);
    setResult(null);
    try {
      const response = await api.trainModel({
        n_users: nUsers,
        n_transactions: nTransactions,
        fraud_rate: fraudRate,
        seed,
      });
      setResult(response);
      onTrained();
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Retrain model" subtitle="/ml/train — regenerates the synthetic dataset and retrains the artefacts.">
      <div className="grid grid--thirds">
        <Field label="Accounts (n_users)">
          <input type="number" min={1} value={nUsers} onChange={(event) => setNUsers(Number(event.target.value))} />
        </Field>
        <Field label="Transactions (n_transactions)">
          <input
            type="number"
            min={1}
            value={nTransactions}
            onChange={(event) => setNTransactions(Number(event.target.value))}
          />
        </Field>
        <Field label="Fraud rate">
          <input
            type="number"
            min={0}
            max={1}
            step={0.001}
            value={fraudRate}
            onChange={(event) => setFraudRate(Number(event.target.value))}
          />
        </Field>
        <Field label="Seed">
          <input type="number" step={1} value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
        </Field>
      </div>
      <div className="row">
        <Button variant="primary" onClick={submit} disabled={busy}>
          {busy ? 'Training…' : 'Retrain model'}
        </Button>
      </div>
      {result && (
        <Notice>
          <strong>{result.status}</strong> — {result.message}{' '}
          {result.regenerate_dataset
            ? 'The dataset was regenerated.'
            : 'The existing dataset was reused.'}
          <pre className="code">{JSON.stringify(result.metrics, null, 2)}</pre>
        </Notice>
      )}
      {failure && <Notice tone="warning">{failure}</Notice>}
    </Card>
  );
}

export function ModelEvaluationPage() {
  const modelInfo = useApiResource(() => api.modelInfo(), []);
  const evaluation = useApiResource(() => api.modelEvaluation(), []);
  const featureImportance = useApiResource(() => api.featureImportance(), []);
  const settings = useApiResource(() => api.settings(), []);

  const reloadAll = () => {
    modelInfo.reload();
    evaluation.reload();
    featureImportance.reload();
    settings.reload();
  };

  const info = modelInfo.data ?? evaluation.data?.model_info ?? null;

  const importanceSource =
    featureImportance.data && featureImportance.data.items.length > 0
      ? featureImportance.data.items
      : (evaluation.data?.feature_importance ?? []);
  const importance: Point[] = [...importanceSource]
    .sort((left, right) => right.importance - left.importance)
    .slice(0, 12)
    .map((item) => ({ label: featureLabel(item.feature), value: item.importance }));

  let body: ReactNode;
  if (modelInfo.error && !info) {
    body = <ErrorState message={modelInfo.error} onRetry={reloadAll} />;
  } else if (evaluation.error && !evaluation.data) {
    body = <ErrorState message={evaluation.error} onRetry={reloadAll} />;
  } else if (!info || !evaluation.data) {
    body = <Loading label="Loading model information…" />;
  } else if (info.models_available && evaluation.data.available) {
    body = <ModelMetrics info={info} evaluation={evaluation.data} importance={importance} />;
  } else {
    body = (
      <UnavailablePanel
        info={info}
        reason={
          !info.models_available
            ? 'No trained model artefacts were found, so evaluation metrics are not available.'
            : 'The evaluation endpoint reported that metrics are not available.'
        }
      />
    );
  }

  return (
    <div className="stack">
      <PageHeader
        title="ML Model Evaluation"
        description={
          <>
            The detection engine fuses a supervised model (the probability of the synthetic fraud label),
            an unsupervised anomaly model (how far a transaction deviates from the account&apos;s own history)
            and deterministic rules. Every metric below is measured on a synthetic held-out split and is not a
            claim of real-world accuracy.
          </>
        }
      />
      {body}
      <TrainingCard settings={settings} onTrained={reloadAll} />
    </div>
  );
}

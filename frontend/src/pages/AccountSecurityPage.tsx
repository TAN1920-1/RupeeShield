/**
 * Account Security Center.
 *
 * The customer's view of the same telemetry the security-operations console
 * reads: sign-ins, failed attempts, devices seen, rows the detector queried, and
 * the incidents opened on their accounts. Every figure comes from the backend's
 * `/api/banking/security-centre`, which derives all of it from stored security
 * events, detections and incidents — nothing here is counted in the browser and
 * nothing is simulated on a timer.
 *
 * The one action on the page writes data on purpose: "Simulate a new sign-in"
 * records a real security event and raises a real notification, both of which
 * then appear below, in the notification centre and on the analyst's overview.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type {
  CustomerIncident,
  CustomerTransaction,
  SecurityFactor,
  SignInEvent,
  SimulatedLogin,
} from '../api/types';
import { AlertCard, SecurityStatusBadge, TransactionTable } from '../components/banking';
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingState,
  Notice,
  PageHeader,
  type Column,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';
import { UNKNOWN, formatDateTime, formatMinor, formatNumber, severityTone, titleCase, type Tone } from '../lib/format';

/** Posture and factor tones are the backend's own vocabulary: GOOD / ATTENTION / DANGER. */
const LEVEL_TONE: Record<string, Tone> = { GOOD: 'allow', ATTENTION: 'flag', ELEVATED: 'block' };
const FACTOR_TONE: Record<string, Tone> = { GOOD: 'allow', ATTENTION: 'flag', DANGER: 'block' };

const SIGN_IN_COLUMNS: Column<SignInEvent>[] = [
  { key: 'timestamp', header: 'When', render: (row) => formatDateTime(row.timestamp) },
  { key: 'event_type', header: 'Event', render: (row) => titleCase(row.event_type) },
  { key: 'description', header: 'Description', render: (row) => row.description },
  {
    key: 'severity',
    header: 'Severity',
    render: (row) => <Badge tone={severityTone(row.severity)}>{row.severity}</Badge>,
  },
  {
    key: 'device',
    header: 'Device',
    render: (row) => <span className="mono">{row.device_id ?? UNKNOWN}</span>,
  },
  { key: 'country', header: 'Country', render: (row) => row.country ?? UNKNOWN },
  {
    key: 'ip',
    header: 'Source (hashed)',
    render: (row) => <span className="mono small">{row.source_ip_hash ?? UNKNOWN}</span>,
  },
];

export function AccountSecurityPage() {
  const navigate = useNavigate();
  const centre = useApiResource(() => api.securityCentre(), []);

  const [simulating, setSimulating] = useState(false);
  const [simulated, setSimulated] = useState<SimulatedLogin | null>(null);
  const [simulateError, setSimulateError] = useState<string | null>(null);

  async function simulateLogin() {
    setSimulating(true);
    setSimulateError(null);
    try {
      const outcome = await api.simulateLogin();
      setSimulated(outcome);
      centre.reload();
    } catch (cause) {
      setSimulateError(cause instanceof Error ? cause.message : 'The event could not be recorded.');
    } finally {
      setSimulating(false);
    }
  }

  function openTransaction(row: CustomerTransaction) {
    navigate(`/banking/transactions?selected=${encodeURIComponent(row.id)}`);
  }

  const data = centre.data;

  return (
    <div className="stack">
      <PageHeader
        title="Account Security"
        description="Sign-ins, devices and payments recorded against your accounts, read from the same security events the investigation console works from. Posture is stated in words, never by colour alone."
        actions={
          <div className="row">
            <Button onClick={centre.reload} disabled={centre.loading}>
              Refresh
            </Button>
            <Button variant="primary" onClick={() => void simulateLogin()} disabled={simulating}>
              {simulating ? 'Recording…' : 'Simulate a new sign-in'}
            </Button>
          </div>
        }
      />

      {simulateError && <ErrorState message={simulateError} />}

      {simulated && (
        <AlertCard title="Simulated sign-in recorded" tone="warning">
          <p className="small" style={{ marginTop: 0 }}>
            {simulated.message}
          </p>
          <KeyValue
            items={[
              { label: 'Event ID', value: <span className="mono">{simulated.event.id}</span> },
              { label: 'Event', value: titleCase(simulated.event.event_type) },
              { label: 'Device', value: <span className="mono">{simulated.device_id}</span> },
              { label: 'Country', value: simulated.country },
              { label: 'Severity', value: <Badge tone={severityTone(simulated.event.severity)}>{simulated.event.severity}</Badge> },
              { label: 'Recorded', value: formatDateTime(simulated.event.timestamp) },
              {
                label: 'Notification',
                value: simulated.notification_id ? (
                  <span className="mono">{simulated.notification_id}</span>
                ) : (
                  'None raised.'
                ),
              },
            ]}
          />
          <p className="small dim" style={{ marginBottom: 0 }}>
            This is a demonstration action, not a background generator: it wrote one security event and
            one notification, and both are now visible here and to the analyst console.
          </p>
        </AlertCard>
      )}

      {centre.loading && !data && <LoadingState label="Loading your security centre…" />}
      {centre.error && <ErrorState message={centre.error} onRetry={centre.reload} />}

      {data && (
        <>
          <AlertCard
            title="Security status"
            tone={
              data.status.level === 'ELEVATED'
                ? 'danger'
                : data.status.level === 'ATTENTION'
                  ? 'warning'
                  : 'info'
            }
            actions={
              <SecurityStatusBadge
                status={data.status.level}
                label={data.status.label}
                tone={LEVEL_TONE[data.status.level] ?? 'neutral'}
              />
            }
          >
            <p className="small" style={{ marginTop: 0 }}>
              {data.status.summary}
            </p>
            <p className="small dim" style={{ marginBottom: 0 }}>
              Computed {formatDateTime(data.generated_at)} from the events, detections and incidents
              stored against your accounts.
            </p>
          </AlertCard>

          <Card
            title="The numbers behind the status"
            subtitle="Each factor states what it counts and what would change it."
          >
            <div className="grid grid--thirds">
              {data.factors.map((factor: SecurityFactor) => (
                <div className="factor" key={factor.key}>
                  <div className="row row--between" style={{ gap: 8 }}>
                    <span className="small">{factor.label}</span>
                    <Badge tone={FACTOR_TONE[factor.tone] ?? 'neutral'}>{formatNumber(factor.value)}</Badge>
                  </div>
                  <p className="small dim" style={{ margin: 0 }}>
                    {factor.detail}
                  </p>
                </div>
              ))}
            </div>
          </Card>

          <div className="grid grid--halves">
            <Card title="Recent sign-ins" subtitle="Successful authentication recorded on your accounts.">
              <DataTable
                columns={SIGN_IN_COLUMNS}
                rows={data.recent_sign_ins}
                keyFor={(row) => row.id}
                emptyTitle="No sign-ins"
                emptyMessage="No sign-in events have been recorded against your accounts yet."
              />
            </Card>

            <Card
              title="Failed sign-in attempts"
              subtitle="Repeated failures raise an alert and a notification."
            >
              <DataTable
                columns={SIGN_IN_COLUMNS}
                rows={data.failed_sign_ins}
                keyFor={(row) => row.id}
                emptyTitle="No failed attempts"
                emptyMessage="No unsuccessful authentication attempts were recorded."
              />
            </Card>
          </div>

          <Card
            title="Devices seen"
            subtitle="Derived from recorded telemetry — a device seen once is not yet recognised."
          >
            <DataTable
              columns={[
                {
                  key: 'device_id',
                  header: 'Device',
                  render: (row) => <span className="mono">{row.device_id}</span>,
                },
                { key: 'country', header: 'Country', render: (row) => row.country ?? UNKNOWN },
                { key: 'last_seen', header: 'Last seen', render: (row) => formatDateTime(row.last_seen) },
                {
                  key: 'recognised',
                  header: 'State',
                  render: (row) => (
                    <Badge tone={row.recognised ? 'allow' : 'flag'}>
                      {row.recognised ? 'Recognised' : 'Seen once'}
                    </Badge>
                  ),
                },
                { key: 'detail', header: 'Note', render: (row) => row.detail },
              ]}
              rows={data.devices}
              keyFor={(row) => row.device_id}
              emptyTitle="No devices"
              emptyMessage="No device telemetry has been recorded against your accounts."
            />
          </Card>

          <Card
            title="Suspicious transactions"
            subtitle="Rows the detector did not allow outright. This is not an assertion of wrongdoing."
          >
            <TransactionTable
              transactions={data.suspicious_transactions}
              onSelect={openTransaction}
            />
            <p className="small dim" style={{ margin: 0 }}>
              Negative results are listed too: every row has a stored outcome, so an all-clear is not
              the absence of data.
            </p>
          </Card>

          <Card
            title="Open incidents"
            subtitle="Investigations raised on your accounts by the correlation engine."
          >
            {data.open_incidents.length === 0 ? (
              <EmptyState
                title="No open incidents"
                message="No correlated investigation is currently open on your accounts."
              />
            ) : (
              <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {data.open_incidents.map((incident: CustomerIncident) => (
                  <li key={incident.id} className="factor">
                    <div className="row row--between" style={{ gap: 8 }}>
                      <span>{incident.title}</span>
                      <span className="row" style={{ gap: 6 }}>
                        <Badge tone={severityTone(incident.severity)}>{incident.severity}</Badge>
                        <SecurityStatusBadge status={incident.status} />
                      </span>
                    </div>
                    <div className="small dim">
                      <span className="mono">{incident.id}</span> · opened {formatDateTime(incident.opened_at)} ·{' '}
                      {incident.event_count} event(s) · {incident.transaction_count} transaction(s) ·
                      suspected loss {formatMinor(incident.suspected_loss_minor)}
                    </div>
                    <div className="small dim">
                      Account <span className="mono">{incident.primary_account_label ?? incident.primary_account_id}</span>
                      {incident.scenario_name ? ` · scenario ${incident.scenario_name}` : ''}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card
            title="Security events"
            subtitle="The most recent authentication, device and payment-integrity telemetry on your accounts."
          >
            <DataTable
              columns={SIGN_IN_COLUMNS}
              rows={data.security_events}
              keyFor={(row) => row.id}
              emptyTitle="No events"
              emptyMessage="No security events have been recorded against your accounts."
            />
          </Card>

          <Notice>
            Your accounts are synthetic and exist only in this database. Nothing on this page is
            published, shared or sent anywhere, and no real account data is involved.
          </Notice>
        </>
      )}
    </div>
  );
}

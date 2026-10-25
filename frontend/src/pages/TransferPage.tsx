/**
 * Simulated bank transfer.
 *
 * The form deliberately performs only the checks it can do honestly in the
 * browser — is a source chosen, is a different recipient chosen, does the amount
 * look like money, does it fit the balance already on screen. Everything that
 * decides whether money actually moves (the transfer bounds, the 24-hour limit,
 * ownership, the risk assessment) is decided by the server, and its message is
 * shown verbatim rather than paraphrased.
 *
 * Duplicate submission is prevented twice over: the confirm button is disabled
 * while a request is in flight, and every attempt carries an idempotency key. The
 * key is regenerated only after a result is shown, so retrying a request whose
 * response was lost replays the original instead of paying twice.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type { AccountCard as AccountCardModel, Payee, TransferResult } from '../api/types';
import {
  Badge,
  Button,
  Card,
  DecisionBadge,
  ErrorState,
  Field,
  KeyValue,
  LoadingState,
  Notice,
  PageHeader,
} from '../components/ui';
import { ConfirmationDialog } from '../components/ConfirmationDialog';
import { AlertCard, RiskScoreBadge, SecurityStatusBadge } from '../components/banking';
import { useApiResource } from '../hooks/useApiResource';
import { formatDateTime, formatMinor, formatMoney } from '../lib/format';

/** Money as typed: a positive decimal with at most two places, or nothing useful. */
const AMOUNT_PATTERN = /^\d{1,12}(\.\d{1,2})?$/;

function newIdempotencyKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ?? `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function amountProblem(value: string, balance: number | null): string | null {
  const trimmed = value.trim();
  if (!trimmed) return 'Enter an amount.';
  if (!AMOUNT_PATTERN.test(trimmed)) return 'Use digits with at most two decimal places, for example 1500.00.';
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return 'The amount must be greater than zero.';
  if (balance !== null && parsed > balance) {
    return `This is more than the current balance of ${formatMoney(balance)}. The server will refuse it.`;
  }
  return null;
}

export function TransferPage() {
  const navigate = useNavigate();

  const accounts = useApiResource(() => api.bankingAccounts(), []);
  const payees = useApiResource(() => api.payees(), []);

  const [sourceId, setSourceId] = useState('');
  const [recipientId, setRecipientId] = useState('');
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TransferResult | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  // Preselect the first account so the common case is one decision, not three.
  useEffect(() => {
    if (sourceId || !accounts.data) return;
    const first = accounts.data.items[0];
    if (first) setSourceId(first.id);
  }, [accounts.data, sourceId]);

  const source: AccountCardModel | null =
    accounts.data?.items.find((account) => account.id === sourceId) ?? null;
  const recipient: Payee | null = payees.data?.find((payee) => payee.id === recipientId) ?? null;

  const problem = amountProblem(amount, source?.balance ?? null);
  const sameAccount = Boolean(sourceId) && sourceId === recipientId;
  const ready = Boolean(sourceId) && Boolean(recipientId) && !sameAccount && problem === null;

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = await api.createTransfer({
        source_account_id: sourceId,
        recipient_account_id: recipientId,
        amount: amount.trim(),
        description: description.trim(),
        idempotency_key: idempotencyKey,
      });
      setResult(outcome);
      setConfirming(false);
      // A new attempt needs a new key; the one that was just used is now spent.
      setIdempotencyKey(newIdempotencyKey());
      setAmount('');
      setDescription('');
      accounts.reload();
    } catch (cause) {
      // The key is kept on purpose: if this failed after the server committed, the
      // retry replays the original transfer rather than sending the money twice.
      setError(cause instanceof Error ? cause.message : 'The transfer could not be submitted.');
    } finally {
      setBusy(false);
    }
  }

  function sendAnother() {
    setResult(null);
    setError(null);
    setIdempotencyKey(newIdempotencyKey());
  }

  return (
    <div className="stack">
      <PageHeader
        title="Send Money"
        description="A simulated transfer between accounts in this database. The debit is scored by the same detection engine the attack simulator uses, and both legs are written in one commit — they settle together or not at all."
        actions={
          <Button onClick={() => navigate('/banking/transactions')}>Transaction history</Button>
        }
      />

      {accounts.loading && !accounts.data && <LoadingState label="Loading your accounts…" />}
      {accounts.error && <ErrorState message={accounts.error} onRetry={accounts.reload} />}

      {accounts.data && accounts.data.items.length === 0 && (
        <Notice tone="warning">
          No accounts are attached to this login, so there is nothing to send from. Run the seeding
          command to create the demo bank.
        </Notice>
      )}

      {accounts.data && accounts.data.items.length > 0 && !result && (
        <Card
          title="New transfer"
          subtitle="The source must be an account you own; the recipient is one of your other accounts or a payee account at this bank."
        >
          <div className="filters">
            <Field label="From" hint={source ? `Balance ${formatMoney(source.balance)}` : undefined}>
              <select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
                <option value="">Choose an account</option>
                {accounts.data.items.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.label} · {account.masked_number} · {formatMoney(account.balance)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="To">
              <select value={recipientId} onChange={(event) => setRecipientId(event.target.value)}>
                <option value="">Choose a recipient</option>
                {(payees.data ?? [])
                  .filter((payee) => payee.id !== sourceId)
                  .map((payee) => (
                    <option key={payee.id} value={payee.id}>
                      {payee.label} · {payee.masked_number}
                    </option>
                  ))}
              </select>
            </Field>

            <Field label="Amount (INR)" hint="Digits with at most two decimal places.">
              <input
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="1500.00"
                inputMode="decimal"
                aria-invalid={problem && amount ? true : undefined}
              />
            </Field>

            <Field label="Description" hint="Optional. It becomes the description on both ledger rows.">
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Rent for October"
                maxLength={140}
              />
            </Field>
          </div>

          {payees.error && <ErrorState message={payees.error} onRetry={payees.reload} />}

          {sameAccount && (
            <Notice tone="warning">Choose a recipient that is different from the source account.</Notice>
          )}
          {problem && amount && <Notice tone="warning">{problem}</Notice>}

          {error && <ErrorState message={error} />}

          <div className="row">
            <Button variant="primary" disabled={!ready || busy} onClick={() => setConfirming(true)}>
              Review transfer
            </Button>
          </div>

          <p className="small dim" style={{ margin: 0 }}>
            Nothing is written until you confirm. The amount is validated on the server, converted to
            paise exactly, and checked against the balance and the daily limit before any money moves.
          </p>
        </Card>
      )}

      {result && (
        <Card
          title="Transfer result"
          subtitle="What the ledger recorded, and what the detector made of it."
          actions={
            <div className="row">
              <Button size="small" onClick={sendAnother}>
                Send another
              </Button>
              <Button size="small" onClick={() => navigate(`/banking/transactions?selected=${encodeURIComponent(result.transactions[0]?.id ?? '')}`)} disabled={result.transactions.length === 0}>
                Open ledger row
              </Button>
            </div>
          }
        >
          <div className="stack">
            {result.duplicate && (
              <Notice tone="warning">
                This submission had already been recorded. The original result is shown and no money
                moved a second time.
              </Notice>
            )}

            <AlertCard
              title={result.status_label}
              tone={result.status === 'BLOCKED' ? 'danger' : result.settled ? 'info' : 'warning'}
              actions={<DecisionBadge decision={result.decision} />}
            >
              <p className="small" style={{ marginTop: 0 }}>
                {result.message}
              </p>
              <div className="row" style={{ gap: 8 }}>
                <SecurityStatusBadge status={result.status} label={result.status_label} />
                <RiskScoreBadge score={result.risk_score} decision={result.decision} />
                <Badge tone="neutral">
                  {formatMinor(result.amount_minor)} {result.currency}
                </Badge>
              </div>
              {!result.settled && (
                <p className="small dim" style={{ marginBottom: 0 }}>
                  No money left the account: the debit leg is recorded but the balance was not
                  reduced, and the row is listed below with its own status.
                </p>
              )}
            </AlertCard>

            <div className="grid grid--halves">
              <Card title="Source account">
                <KeyValue
                  items={[
                    { label: 'Account', value: result.source.label },
                    { label: 'Number', value: <span className="mono">{result.source.masked_number}</span> },
                    { label: 'Debited', value: formatMinor(result.source.amount_minor) },
                    { label: 'Balance after', value: formatMoney(result.source.balance) },
                  ]}
                />
              </Card>
              <Card title="Recipient account">
                <KeyValue
                  items={[
                    { label: 'Account', value: result.recipient.label },
                    { label: 'Number', value: <span className="mono">{result.recipient.masked_number}</span> },
                    { label: 'Credited', value: formatMinor(result.recipient.amount_minor) },
                    { label: 'Balance after', value: formatMoney(result.recipient.balance) },
                  ]}
                />
              </Card>
            </div>

            <Card title="Detection" subtitle="The reasons and rules the engine reported for the debit leg.">
              <KeyValue
                items={[
                  { label: 'Transfer ID', value: <span className="mono">{result.transfer_id}</span> },
                  { label: 'Decision', value: <DecisionBadge decision={result.decision} /> },
                  { label: 'Risk score', value: <RiskScoreBadge score={result.risk_score} /> },
                  { label: 'Recorded', value: formatDateTime(result.created_at) },
                  { label: 'Description', value: result.description || '—' },
                  {
                    label: 'Reasons',
                    value:
                      result.reasons.length > 0 ? (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {result.reasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      ) : (
                        'None reported.'
                      ),
                  },
                  {
                    label: 'Rules triggered',
                    value:
                      result.rules_triggered.length > 0
                        ? result.rules_triggered.join(', ')
                        : 'None.',
                  },
                ]}
              />
              <p className="small dim" style={{ margin: 0 }}>
                The submission is written to the audit log against this transfer id, with the account
                pair, the amount, the decision and the risk score — and no credential material.
              </p>
            </Card>

            <Card
              title="Ledger rows"
              subtitle="Both legs of this transfer, as stored. Opening one shows its detection evidence."
            >
              <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {result.transactions.map((row) => (
                  <li key={row.id} className="factor">
                    <div className="row row--between" style={{ gap: 8 }}>
                      <span className="mono small">{row.id}</span>
                      <SecurityStatusBadge status={row.status} label={row.status_label} />
                    </div>
                    <div className="small dim">
                      {row.description} · {formatMinor(row.amount_minor)} · {formatDateTime(row.timestamp)}
                    </div>
                    <div className="row">
                      <Button
                        size="small"
                        variant="ghost"
                        onClick={() =>
                          navigate(`/banking/transactions?selected=${encodeURIComponent(row.id)}`)
                        }
                      >
                        Open in history
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
              {result.notification_id && (
                <p className="small dim" style={{ marginBottom: 0 }}>
                  A notification was raised for this transfer ({result.notification_id}); it appears in
                  the notification centre for the account holder.
                </p>
              )}
            </Card>
          </div>
        </Card>
      )}

      <ConfirmationDialog
        open={confirming}
        title="Confirm this transfer"
        confirmLabel="Send money"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={() => void submit()}
      >
        <p className="small" style={{ margin: 0 }}>
          The source account is debited and the recipient credited in one commit. If the detector
          blocks or holds the payment, neither balance changes.
        </p>
        <KeyValue
          items={[
            { label: 'From', value: source ? `${source.label} · ${source.masked_number}` : '—' },
            { label: 'To', value: recipient ? `${recipient.label} · ${recipient.masked_number}` : '—' },
            { label: 'Amount', value: amount.trim() ? `${amount.trim()} INR` : '—' },
            { label: 'Description', value: description.trim() || 'Transfer' },
            {
              label: 'Source balance after',
              value:
                source && amount.trim()
                  ? formatMoney(source.balance - Number(amount.trim()))
                  : '—',
            },
          ]}
        />
      </ConfirmationDialog>
    </div>
  );
}

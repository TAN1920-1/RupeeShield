/**
 * Transaction history.
 *
 * The customer's own ledger, page by page, with the filters the specification
 * asks for: free-text search, date range, account, type (debit/credit),
 * settlement status and whether the detector queried the row. Every request goes
 * through `/api/banking/transactions`, which scopes to the signed-in customer
 * inside the query — the account filter can only narrow that scope, never widen
 * it, so an id typed into the box returns nothing rather than somebody else's
 * statement.
 *
 * The selected row lives in the URL (`?selected=<id>`) rather than in component
 * state, so a link from the dashboard, the security centre or a notification
 * opens the same detail panel — and the browser's back button closes it.
 */
import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import type { CustomerTransaction, CustomerTransactionDetail } from '../api/types';
import { TransactionDetails, TransactionTable } from '../components/banking';
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Loading,
  PageHeader,
} from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';

/** The stored settlement states the backend accepts on the `status` filter. */
const STATUSES = ['SETTLED', 'BLOCKED', 'PENDING_REVIEW', 'REVERSED'] as const;
const DIRECTIONS = ['DEBIT', 'CREDIT'] as const;
const LIMIT_OPTIONS = [10, 25, 50, 100];

export function TransactionHistoryPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('selected');
  const urlSearch = params.get('search') ?? '';

  // `searchDraft` is what is being typed; `search` is what has been sent to the
  // API. Splitting them keeps a request from being fired per keystroke and means
  // the row is only filtered once the reader asks for it.
  const [searchDraft, setSearchDraft] = useState(urlSearch);
  const [search, setSearch] = useState(urlSearch);
  const [accountId, setAccountId] = useState('');
  const [direction, setDirection] = useState('');
  const [status, setStatus] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [onlyQueried, setOnlyQueried] = useState(false);
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);

  // A notification opens this page with `?search=<id>`. Applying it here means
  // the row the alert was about is on screen when the page appears.
  useEffect(() => {
    if (!urlSearch) return;
    setSearchDraft(urlSearch);
    setSearch(urlSearch);
    setOffset(0);
  }, [urlSearch]);

  const list = useApiResource(
    () =>
      api.bankingTransactions({
        search: search || undefined,
        account_id: accountId || undefined,
        direction: direction || undefined,
        status: status || undefined,
        suspicious: onlyQueried ? true : undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        limit,
        offset,
      }),
    [search, accountId, direction, status, onlyQueried, dateFrom, dateTo, limit, offset],
  );

  const accounts = useApiResource(() => api.bankingAccounts(), []);

  const detail = useApiResource<CustomerTransactionDetail | null>(
    () => (selectedId ? api.bankingTransaction(selectedId) : Promise.resolve(null)),
    [selectedId],
  );

  const total = list.data?.total ?? 0;
  const returned = list.data?.returned ?? 0;
  const filtersActive =
    Boolean(search) ||
    Boolean(accountId) ||
    Boolean(direction) ||
    Boolean(status) ||
    Boolean(dateFrom) ||
    Boolean(dateTo) ||
    onlyQueried;

  /** Any filter change invalidates the page number, so the offset is reset with it. */
  function change<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setOffset(0);
    };
  }

  function applySearch(event: FormEvent) {
    event.preventDefault();
    setSearch(searchDraft.trim());
    setOffset(0);
  }

  function resetFilters() {
    setSearchDraft('');
    setSearch('');
    setAccountId('');
    setDirection('');
    setStatus('');
    setDateFrom('');
    setDateTo('');
    setOnlyQueried(false);
    setLimit(25);
    setOffset(0);
  }

  function openRow(row: CustomerTransaction) {
    const next = new URLSearchParams(params);
    next.set('selected', row.id);
    setParams(next);
  }

  function closeDetail() {
    const next = new URLSearchParams(params);
    next.delete('selected');
    setParams(next, { replace: true });
  }

  return (
    <div className="stack">
      <PageHeader
        title="Transaction History"
        description="Every row on your accounts, newest first. A row the detector queried keeps its own record — open it to see the score, the feature contributions behind it and the incident it belongs to."
        actions={
          <Button onClick={resetFilters} disabled={!filtersActive}>
            Clear filters
          </Button>
        }
      />

      <Card title="Filters" subtitle="Search and narrow the ledger; the account scope is always applied first.">
        <form className="filters" onSubmit={applySearch}>
          <Field label="Search" hint="Matches the id, description, counterparty, channel or status.">
            <input
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder="TX-…, salary, rent"
            />
          </Field>

          <Field label="Account">
            <select value={accountId} onChange={(event) => change(setAccountId)(event.target.value)}>
              <option value="">All accounts</option>
              {(accounts.data?.items ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} · {account.masked_number}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Type">
            <select value={direction} onChange={(event) => change(setDirection)(event.target.value)}>
              <option value="">All types</option>
              {DIRECTIONS.map((value) => (
                <option key={value} value={value}>
                  {value === 'DEBIT' ? 'Debit (outgoing)' : 'Credit (incoming)'}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Status">
            <select value={status} onChange={(event) => change(setStatus)(event.target.value)}>
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
          </Field>

          <Field label="From">
            <input
              type="date"
              value={dateFrom}
              onChange={(event) => change(setDateFrom)(event.target.value)}
            />
          </Field>

          <Field label="To">
            <input
              type="date"
              value={dateTo}
              onChange={(event) => change(setDateTo)(event.target.value)}
            />
          </Field>

          <Field label="Page size">
            <select value={limit} onChange={(event) => change(setLimit)(Number(event.target.value))}>
              {LIMIT_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Detector">
            <span className="row" style={{ gap: 8 }}>
              <input
                type="checkbox"
                checked={onlyQueried}
                onChange={(event) => change(setOnlyQueried)(event.target.checked)}
              />
              <span className="small">Only rows the detector did not allow outright</span>
            </span>
          </Field>

          <div className="row" style={{ alignItems: 'flex-end' }}>
            <Button type="submit">Apply search</Button>
          </div>
        </form>
      </Card>

      <Card
        title="Ledger"
        subtitle="Click a row to open its detection breakdown. Status reads in words as well as colour."
      >
        <TransactionTable
          transactions={list.data?.items ?? []}
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          onSelect={openRow}
          caption={list.data ? `${returned} of ${total} row(s) on this page` : undefined}
        />
        <div className="pagination">
          <span className="small dim">
            Showing {returned} of {total} row(s)
            {filtersActive ? ' matching the current filters' : ''}
          </span>
          <div className="row">
            <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              Previous
            </Button>
            <Button
              size="small"
              disabled={total <= offset + returned || returned === 0}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      {selectedId && (
        <Card
          title={`Transaction ${selectedId}`}
          subtitle="The stored row, the detection that scored it and the records linked to it."
          actions={
            <Button size="small" variant="ghost" onClick={closeDetail}>
              Close
            </Button>
          }
        >
          {detail.loading && <Loading label="Loading transaction…" />}
          {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}
          {!detail.loading && !detail.error && !detail.data && (
            <EmptyState
              title="No detail"
              message="No detail record was returned for this transaction."
            />
          )}
          {detail.data && !detail.loading && !detail.error && <TransactionDetails detail={detail.data} />}
        </Card>
      )}
    </div>
  );
}

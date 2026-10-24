/**
 * Component tests for the shared table/badge primitives.
 *
 * These cover the states a page is most likely to get wrong: an empty result set
 * must say so rather than rendering an empty box, a fetch failure must surface the
 * server's message verbatim, and a decision badge must carry the right tone class so
 * the colour is never the only signal.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { DataTable, DecisionBadge, StatusBadge, type Column } from './ui';

interface Row {
  id: string;
  amount: number;
}

const columns: Column<Row>[] = [
  { key: 'id', header: 'Transaction', render: (row) => row.id },
  { key: 'amount', header: 'Amount', numeric: true, render: (row) => String(row.amount) },
];

const rows: Row[] = [
  { id: 'TXN-1', amount: 100 },
  { id: 'TXN-2', amount: 250 },
];

describe('DataTable', () => {
  it('renders a row per record', () => {
    render(<DataTable<Row> columns={columns} rows={rows} keyFor={(row) => row.id} />);
    expect(screen.getByText('TXN-1')).toBeTruthy();
    expect(screen.getByText('TXN-2')).toBeTruthy();
  });

  it('explains an empty result instead of rendering nothing', () => {
    render(
      <DataTable<Row>
        columns={columns}
        rows={[]}
        keyFor={(row) => row.id}
        emptyTitle="No transactions"
        emptyMessage="Nothing matches the current filters."
      />,
    );
    expect(screen.getByText('No transactions')).toBeTruthy();
    expect(screen.getByText('Nothing matches the current filters.')).toBeTruthy();
  });

  it('surfaces the server error verbatim and offers a retry', () => {
    const onRetry = vi.fn();
    render(
      <DataTable<Row> columns={columns} rows={[]} keyFor={(row) => row.id} error="503 unavailable" onRetry={onRetry} />,
    );
    expect(screen.getByText('503 unavailable')).toBeTruthy();
    screen.getByRole('button', { name: /try again/i }).click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe('badges', () => {
  it('carries the decision in the class name as well as the text', () => {
    const { container } = render(<DecisionBadge decision="BLOCK" />);
    expect(screen.getByText('BLOCK')).toBeTruthy();
    expect(container.querySelector('.badge--block')).toBeTruthy();
  });

  it('renders an unknown decision as a neutral placeholder', () => {
    const { container } = render(<DecisionBadge decision={null} />);
    expect(container.querySelector('.badge--neutral')).toBeTruthy();
  });

  it('humanises run statuses', () => {
    render(<StatusBadge status="PENDING_REVIEW" />);
    expect(screen.getByText('PENDING REVIEW')).toBeTruthy();
  });
});

/**
 * Shared presentational primitives.
 *
 * Every page composes these so spacing, colour and state handling stay
 * consistent. Nothing here fetches data — pages own their data via `useApiResource`.
 */
import type { CSSProperties, ReactNode } from 'react';
import type { Tone } from '../lib/format';
import { decisionTone, UNKNOWN } from '../lib/format';

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  style,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <section className={`card ${className}`} style={style} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || actions) && (
        <header className="card__header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="card__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  const colour: Record<Tone, string> = {
    allow: 'var(--success)',
    flag: 'var(--amber)',
    block: 'var(--danger)',
    info: 'var(--cyan)',
    neutral: 'var(--text)',
  };
  return (
    <div className="card stat" style={{ borderLeft: `2px solid ${colour[tone]}` }}>
      <span className="stat__label">{label}</span>
      <span className="stat__value" style={{ color: colour[tone] }}>
        {value}
      </span>
      {hint && <span className="stat__hint">{hint}</span>}
    </div>
  );
}

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <Badge tone="neutral">{UNKNOWN}</Badge>;
  return <Badge tone={decisionTone(decision)}>{decision}</Badge>;
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge tone="neutral">{UNKNOWN}</Badge>;
  const tone: Tone =
    status === 'COMPLETED' || status === 'SETTLED' || status === 'BLOCKED'
      ? status === 'BLOCKED'
        ? 'block'
        : 'allow'
      : status === 'RUNNING' || status === 'PENDING_REVIEW'
        ? 'info'
        : status === 'FAILED'
          ? 'block'
          : 'neutral';
  return <Badge tone={tone}>{status.replace(/_/g, ' ')}</Badge>;
}

export function Button({
  children,
  onClick,
  variant = 'default',
  size = 'default',
  disabled,
  type = 'button',
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'default' | 'primary' | 'danger' | 'ghost';
  size?: 'default' | 'small';
  disabled?: boolean;
  type?: 'button' | 'submit';
  title?: string;
}) {
  const classes = [
    'button',
    variant !== 'default' ? `button--${variant}` : '',
    size === 'small' ? 'button--small' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button className={classes} onClick={onClick} disabled={disabled} type={type} title={title}>
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={tab.key === active}
          className={`tab ${tab.key === active ? 'tab--active' : ''}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="state">
      <span className="state__title">{title}</span>
      <span>{message}</span>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state--error" role="alert">
      <span className="state__title">Something went wrong</span>
      <span>{message}</span>
      {onRetry && (
        <Button size="small" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <span className="rows-loading">
      <span className="spinner" aria-hidden="true" />
      {label}
    </span>
  );
}

/**
 * Page-level loading placeholder, as distinct from the inline `Loading` spinner:
 * it occupies the same space the content will, so a page does not jump when the
 * request resolves.
 */
export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <Loading label={label} />
    </div>
  );
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'warning' }) {
  return <div className={`notice ${tone === 'warning' ? 'notice--warning' : ''}`}>{children}</div>;
}

export function ProgressBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function KeyValue({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="kv">
      {items.map((item) => (
        <div key={item.label} style={{ display: 'contents' }}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Legend({ items }: { items: { label: string; colour: string }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="legend__item" key={item.label}>
          <span className="legend__swatch" style={{ background: item.colour }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  numeric?: boolean;
  width?: string;
}

export function DataTable<T>({
  columns,
  rows,
  keyFor,
  emptyTitle = 'No records',
  emptyMessage = 'Nothing matches the current filters.',
  loading,
  error,
  onRetry,
  onRowClick,
  caption,
}: {
  columns: Column<T>[];
  rows: T[];
  keyFor: (row: T) => string;
  emptyTitle?: string;
  emptyMessage?: string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onRowClick?: (row: T) => void;
  caption?: string;
}) {
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (loading && rows.length === 0) return <Loading />;
  if (!loading && rows.length === 0) return <EmptyState title={emptyTitle} message={emptyMessage} />;

  return (
    <div className="table-wrap">
      <table className="data">
        {caption && <caption className="dim small" style={{ textAlign: 'left', padding: '0 0 6px' }}>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} style={column.width ? { width: column.width } : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={keyFor(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              style={onRowClick ? { cursor: 'pointer' } : undefined}
            >
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? 'numeric' : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {loading && rows.length > 0 && (
        <div style={{ padding: '8px 12px' }}>
          <Loading label="Refreshing…" />
        </div>
      )}
    </div>
  );
}

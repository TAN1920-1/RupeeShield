/**
 * Presentation helpers.
 *
 * Rule followed everywhere in this dashboard: an unknown metric renders as "—",
 * never as "0". The backend sends `null` for undefined values (for example
 * precision on an all-negative test slice) and silently turning that into zero
 * would be a lie.
 */

const CURRENCY = 'INR';

const moneyFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: CURRENCY,
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

const compactMoneyFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: CURRENCY,
  notation: 'compact',
  maximumFractionDigits: 1,
});

const numberFormatter = new Intl.NumberFormat('en-IN');

export const UNKNOWN = '—';

export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return moneyFormatter.format(value);
}

export function formatMoneyCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return compactMoneyFormatter.format(value);
}

export function formatMinor(minor: number | null | undefined): string {
  if (minor === null || minor === undefined) return UNKNOWN;
  return formatMoney(minor / 100);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return numberFormatter.format(value);
}

export function formatMetric(
  value: number | null | undefined,
  digits = 3,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return value.toFixed(digits);
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return UNKNOWN;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleString('en-IN', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return UNKNOWN;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleTimeString('en-IN', { hour12: false });
}

export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return UNKNOWN;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export type Tone = 'allow' | 'flag' | 'block' | 'neutral' | 'info';

export function decisionTone(decision: string | null | undefined): Tone {
  switch ((decision ?? '').toUpperCase()) {
    case 'ALLOW':
      return 'allow';
    case 'FLAG':
      return 'flag';
    case 'BLOCK':
      return 'block';
    default:
      return 'neutral';
  }
}

export function riskTone(score: number | null | undefined): Tone {
  if (score === null || score === undefined) return 'neutral';
  if (score >= 0.8) return 'block';
  if (score >= 0.45) return 'flag';
  return 'allow';
}

export function severityTone(severity: string | null | undefined): Tone {
  switch ((severity ?? '').toUpperCase()) {
    case 'CRITICAL':
    case 'HIGH':
      return 'block';
    case 'MEDIUM':
    case 'LOW':
      return 'flag';
    case 'INFO':
      return 'info';
    default:
      return 'neutral';
  }
}

export function riskStatusLabel(status: string): string {
  switch (status) {
    case 'FLAGGED_FOR_INVESTIGATION':
      return 'Flagged for investigation';
    case 'ELEVATED_SIMULATED_RISK':
      return 'Elevated simulated risk';
    default:
      return 'Normal';
  }
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** Feature names as they should be read by a human in a chart or table. */
export function featureLabel(feature: string): string {
  const known: Record<string, string> = {
    txns_last_10min: 'Transactions in last 10 min',
    txns_last_1h: 'Transactions in last hour',
    txns_last_24h: 'Transactions in last 24 h',
    time_since_prev_txn_min: 'Minutes since previous transaction',
    amount_over_user_avg: 'Amount vs account average',
    amount_zscore: 'Amount z-score',
    user_avg_amount: 'Account average amount',
    is_new_device: 'New device',
    is_new_country: 'New country',
    is_channel_switch: 'Channel switch',
    is_late_night: 'Late-night transaction',
    is_weekend: 'Weekend',
    day_of_week: 'Day of week',
    hour: 'Hour of day',
    amount: 'Amount',
  };
  return known[feature] ?? titleCase(feature);
}

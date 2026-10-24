/**
 * Unit tests for the presentation helpers.
 *
 * The rule these lock in is the one most likely to be broken by a careless edit:
 * an unknown metric renders as "—", never as "0" or "NaN". The backend sends `null`
 * for genuinely undefined values, and turning that into zero would misreport an
 * undefined precision as a measured zero.
 */
import { describe, expect, it } from 'vitest';

import {
  UNKNOWN,
  decisionTone,
  featureLabel,
  formatMetric,
  formatMinor,
  formatNumber,
  formatPercent,
  riskStatusLabel,
  riskTone,
  severityTone,
  titleCase,
} from './format';

describe('unknown handling', () => {
  it('renders null and undefined as the unknown marker, not as zero', () => {
    expect(formatNumber(null)).toBe(UNKNOWN);
    expect(formatNumber(undefined)).toBe(UNKNOWN);
    expect(formatMetric(null)).toBe(UNKNOWN);
    expect(formatPercent(undefined)).toBe(UNKNOWN);
    expect(formatMinor(null)).toBe(UNKNOWN);
  });

  it('still renders a real zero', () => {
    expect(formatNumber(0)).toBe('0');
    expect(formatPercent(0)).toBe('0.0%');
  });
});

describe('money formatting', () => {
  it('converts minor units (paise) to a major-unit currency string', () => {
    // 1 INR = 100 paise, so 123_456 paise is INR 1,234.56.
    expect(formatMinor(123_456)).toMatch(/1,234\.56/);
    expect(formatMinor(0)).toMatch(/0\.00/);
  });

  it('formats a metric to the requested number of digits', () => {
    expect(formatMetric(0.12345, 3)).toBe('0.123');
    expect(formatMetric(0.12345, 4)).toBe('0.1235');
  });
});

describe('tone mapping', () => {
  it('maps decisions onto the badge vocabulary', () => {
    expect(decisionTone('ALLOW')).toBe('allow');
    expect(decisionTone('FLAG')).toBe('flag');
    expect(decisionTone('BLOCK')).toBe('block');
    expect(decisionTone(null)).toBe('neutral');
  });

  it('maps a risk score onto the same three bands the engine uses', () => {
    expect(riskTone(0.1)).toBe('allow');
    expect(riskTone(0.5)).toBe('flag');
    expect(riskTone(0.95)).toBe('block');
    expect(riskTone(null)).toBe('neutral');
  });

  it('maps severity, treating high and critical alike', () => {
    expect(severityTone('CRITICAL')).toBe('block');
    expect(severityTone('HIGH')).toBe('block');
    expect(severityTone('MEDIUM')).toBe('flag');
    expect(severityTone('INFO')).toBe('info');
  });
});

describe('account-facing wording', () => {
  it('never describes an account as criminal', () => {
    const labels = [
      riskStatusLabel('FLAGGED_FOR_INVESTIGATION'),
      riskStatusLabel('ELEVATED_SIMULATED_RISK'),
      riskStatusLabel('NORMAL'),
    ];
    expect(labels).toEqual(['Flagged for investigation', 'Elevated simulated risk', 'Normal']);
    labels.forEach((label) => {
      expect(label.toLowerCase()).not.toContain('criminal');
      expect(label.toLowerCase()).not.toContain('fraudster');
    });
  });
});

describe('label helpers', () => {
  it('turns machine keys into readable names', () => {
    expect(titleCase('ACCOUNT_TAKEOVER')).toBe('Account Takeover');
    expect(titleCase('flagged for investigation')).toBe('Flagged For Investigation');
  });

  it('explains known feature names and falls back for unknown ones', () => {
    expect(featureLabel('txns_last_10min')).toBe('Transactions in last 10 min');
    expect(featureLabel('some_new_feature')).toBe('Some New Feature');
  });
});

/**
 * Chart wrappers around Recharts.
 *
 * The point of wrapping is that every chart in the app reads the same way: muted
 * grid, tabular axis labels, a dark tooltip, and a consistent palette. Pages pass
 * pre-shaped `{ label, value }` points, so no page needs to know Recharts' API.
 */
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { UNKNOWN, formatNumber } from '../lib/format';

const GRID = '#24334e';
const AXIS_TICK = { fill: '#93a4bd', fontSize: 11 } as const;
const TOOLTIP_STYLE = {
  background: '#0b1220',
  border: '1px solid #33456a',
  borderRadius: 8,
  fontSize: 12,
  color: '#e9eff8',
} as const;

export interface Point {
  label: string;
  value: number;
}

export function TrendChart({
  points,
  colour = '#22d3ee',
  height = 220,
  valueFormatter = formatNumber,
}: {
  points: Point[];
  colour?: string;
  height?: number;
  valueFormatter?: (value: number | null | undefined) => string;
}) {
  if (points.length === 0) {
    return <p className="dim small">No time series to plot yet.</p>;
  }
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={24} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={54} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: '#93a4bd' }}
            formatter={(value) => valueFormatter(typeof value === 'number' ? value : Number(value))}
          />
          <Line type="monotone" dataKey="value" stroke={colour} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function BreakdownChart({
  points,
  height = 220,
  colours,
  horizontal = false,
  valueFormatter = formatNumber,
}: {
  points: Point[];
  height?: number;
  colours?: string[];
  horizontal?: boolean;
  valueFormatter?: (value: number | null | undefined) => string;
}) {
  if (points.length === 0) {
    return <p className="dim small">No breakdown to plot yet.</p>;
  }
  const palette = colours ?? ['#22d3ee', '#14b8a6', '#f5b942', '#ef4d4d', '#8b95ff', '#35c46a'];
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={points}
          layout={horizontal ? 'vertical' : 'horizontal'}
          margin={{ top: 8, right: 16, bottom: 4, left: horizontal ? 8 : 0 }}
        >
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: GRID }} />
              <YAxis type="category" dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} width={140} />
            </>
          ) : (
            <>
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: GRID }} interval={0} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={54} />
            </>
          )}
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: '#93a4bd' }}
            cursor={{ fill: 'rgba(34, 211, 238, 0.06)' }}
            formatter={(value) => valueFormatter(typeof value === 'number' ? value : Number(value))}
          />
          <Bar dataKey="value" radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false}>
            {points.map((point, index) => (
              <Cell key={point.label} fill={palette[index % palette.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Precision-recall curve. The x axis is recall and the y axis is precision, which
 * is why this cannot reuse `TrendChart` (whose axes are index/label based).
 */
export function PrCurveChart({
  recall,
  precision,
  averagePrecision,
  height = 240,
}: {
  recall: number[];
  precision: number[];
  averagePrecision: number | null;
  height?: number;
}) {
  const points = recall.map((value, index) => ({ label: value, value: precision[index] ?? 0 }));
  if (points.length === 0) {
    return <p className="dim small">No precision-recall data available.</p>;
  }
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="label"
            domain={[0, 1]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            tickFormatter={(value: number) => value.toFixed(1)}
          />
          <YAxis
            domain={[0, 1]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(value: number) => value.toFixed(1)}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: '#93a4bd' }}
            labelFormatter={(value) => `recall ${Number(value).toFixed(3)}`}
            formatter={(value) => `precision ${(typeof value === 'number' ? value : Number(value)).toFixed(3)}`}
          />
          <Line type="monotone" dataKey="value" stroke="#14b8a6" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <p className="dim small" style={{ marginTop: 6 }}>
        Average precision (area under the PR curve):{' '}
        <strong>{averagePrecision === null ? UNKNOWN : averagePrecision.toFixed(3)}</strong>
      </p>
    </div>
  );
}

/**
 * Incident timeline.
 *
 * One entry per correlated record — a security event, a transaction, a detection
 * or a defense action — in the order the incident produced them. Extracted so the
 * same rendering serves every view that shows an incident's history; the marker
 * colour is keyed off `entry.kind` and the kind is also written out as text, so
 * the timeline is readable without relying on colour.
 */
import type { IncidentTimelineEntry } from '../api/types';
import { formatDateTime } from '../lib/format';
import { Notice } from './ui';

export function IncidentTimeline({ entries }: { entries: IncidentTimelineEntry[] }) {
  if (entries.length === 0) {
    return <Notice>No timeline entries are recorded for this incident.</Notice>;
  }

  return (
    <div className="timeline">
      {entries.map((entry) => (
        <div key={entry.id} className={`timeline__item timeline__item--${entry.kind}`}>
          <div className="timeline__meta">
            <span className="mono">#{entry.seq}</span>
            <span>{formatDateTime(entry.timestamp)}</span>
            <span>{entry.kind}</span>
            {entry.ref_id && <span className="mono">{entry.ref_id}</span>}
          </div>
          <div>{entry.description}</div>
          {Object.keys(entry.payload).length > 0 && (
            <pre className="code">{JSON.stringify(entry.payload, null, 2).slice(0, 800)}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

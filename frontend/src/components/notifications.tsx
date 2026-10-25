/**
 * Notification centre.
 *
 * Every row in this panel was written by the backend at the moment something
 * happened: a transfer that was blocked or held, a sign-in from an unrecognised
 * device, a burst of failed password attempts, an attack scenario that raised an
 * incident. The panel polls; it never manufactures an alert on a timer.
 */
import { useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type { NotificationItem } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useApiResource, useInterval } from '../hooks/useApiResource';
import { formatDateTime, severityTone } from '../lib/format';
import { Popover } from './Popover';
import { Badge, Button, EmptyState, ErrorState, Loading } from './ui';

/** Where a notification points, for the role that is reading it. */
function targetFor(item: NotificationItem, analyst: boolean): string {
  if (item.entity_type === 'transfer') {
    // A customer can open the row itself; the analyst console is a whole-bank view
    // and is filtered by run/decision instead, so it opens the explorer.
    return analyst
      ? '/transactions'
      : `/banking/transactions?search=${encodeURIComponent(item.entity_id ?? '')}`;
  }
  if (item.entity_type === 'security_event' || item.kind.startsWith('SECURITY')) {
    return analyst ? '/overview' : '/banking/security';
  }
  return analyst ? '/overview' : '/banking';
}

export function NotificationPanel() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const feed = useApiResource(() => api.notifications({ limit: 20 }), []);
  useInterval(feed.reload, 20_000);

  const unread = feed.data?.unread ?? 0;

  function open(item: NotificationItem, close: () => void) {
    close();
    if (!item.read) {
      // Best effort: the row still opens, and the next poll corrects the badge.
      void api
        .markNotificationRead(item.id)
        .then(() => feed.reload())
        .catch(() => undefined);
    }
    navigate(targetFor(item, user?.role === 'ANALYST'));
  }

  return (
    <Popover
      triggerLabel={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
      onOpen={feed.reload}
      panelClassName="notification-panel"
      trigger={() => (
        <span style={{ position: 'relative', display: 'inline-flex' }}>
          Notifications
          {unread > 0 && <span className="bell__badge">{unread > 99 ? '99+' : unread}</span>}
        </span>
      )}
    >
      {(close) => (
        <>
          <div className="notification-panel__head">
            <strong className="small">Notifications</strong>
            <Button
              size="small"
              variant="ghost"
              disabled={unread === 0}
              onClick={() => {
                void api
                  .markAllNotificationsRead()
                  .then(() => feed.reload())
                  .catch(() => undefined);
              }}
            >
              Mark all read
            </Button>
          </div>

          {feed.loading && !feed.data && <Loading label="Loading notifications…" />}
          {feed.error && <ErrorState message={feed.error} onRetry={feed.reload} />}
          {feed.data && feed.data.items.length === 0 && (
            <EmptyState
              title="Nothing to report"
              message="An alert appears here when the backend records one: a transfer that was blocked or held, a sign-in from a new device, or repeated failed password attempts."
            />
          )}

          {feed.data && feed.data.items.length > 0 && (
            <div className="notification-panel__list">
              {feed.data.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`notification-panel__item ${item.read ? '' : 'notification-panel__item--unread'}`}
                  onClick={() => open(item, close)}
                >
                  <span className="notification-panel__title">
                    <Badge tone={severityTone(item.severity)}>{item.severity}</Badge>
                    <span>{item.title}</span>
                  </span>
                  {item.body && <span className="small dim">{item.body}</span>}
                  <span className="mono small dim">
                    {formatDateTime(item.created_at)}
                    {item.read ? '' : ' · unread'}
                    {item.entity_id ? ` · ${item.entity_id}` : ''}
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </Popover>
  );
}

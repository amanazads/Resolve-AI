import React, { useState, useEffect, useCallback } from 'react';
import { Activity, RefreshCw, Radio } from 'lucide-react';
import { useAsync } from '../../hooks/useAsync';
import { listAuditLog, listCampaignActivity, listGlobalCampaignActivity } from '../../services/api';
import { useCampaignEvents } from '../../hooks/useCampaignEvents';
import { Card, PageHeader, Timeline, formatTime } from '../ui/Primitives';
import { EmptyState, ErrorState, LoadingState } from '../ui/States';

const CAMPAIGN_FILTERS = [
  { key: '', label: 'All Events' },
  { key: 'MESSAGE_SENT', label: 'Sent' },
  { key: 'MESSAGE_FAILED', label: 'Failed' },
  { key: 'MESSAGE_RETRIED', label: 'Retried' },
  { key: 'MESSAGE_GENERATED', label: 'Generated' },
  { key: 'CAMPAIGN_STARTED', label: 'Started' },
  { key: 'CAMPAIGN_COMPLETED', label: 'Completed' },
  { key: 'DRY_RUN_COMPLETED', label: 'Dry Run' }
];

const GLOBAL_FILTERS = [
  { key: '', label: 'Everything' },
  { key: 'ACTION_SUCCEEDED', label: 'Actions' },
  { key: 'TASK_COMPLETED', label: 'Tasks' },
  { key: 'AUTHORIZATION_REQUESTED', label: 'Authorizations' },
  { key: 'MESSAGE_SENT', label: 'Sent' },
  { key: 'INTEGRATION_CONNECTED', label: 'Integrations' }
];

const TONES = {
  TASK_COMPLETED: 'success',
  ACTION_SUCCEEDED: 'success',
  AUTHORIZATION_GRANTED: 'success',
  MESSAGE_SENT: 'success',
  DRY_RUN_COMPLETED: 'success',
  CAMPAIGN_COMPLETED: 'success',
  PERMISSION_GRANTED: 'success',
  INTEGRATION_CONNECTED: 'success',
  TASK_CREATED: 'brand',
  FILES_INSPECTED: 'brand',
  PLAN_CREATED: 'brand',
  TASK_RESUMED: 'brand',
  ACTION_STARTED: 'brand',
  CAMPAIGN_STARTED: 'brand',
  CAMPAIGN_CREATED: 'brand',
  PLAN_GENERATED: 'brand',
  CAMPAIGN_RESUMED: 'brand',
  MESSAGE_GENERATED: 'brand',
  PERMISSION_USED: 'brand',
  CLARIFICATION_REQUESTED: 'warn',
  AUTHORIZATION_REQUESTED: 'warn',
  TASK_PAUSED: 'warn',
  MESSAGE_RETRIED: 'warn',
  CAMPAIGN_PAUSED: 'warn',
  PERMISSION_REVOKED: 'warn',
  INTEGRATION_DISCONNECTED: 'warn',
  TASK_FAILED: 'danger',
  ACTION_FAILED: 'danger',
  MESSAGE_FAILED: 'danger',
  CAMPAIGN_CANCELLED: 'danger',
  PERMISSION_DENIED: 'danger'
};

const title = (event) => (event || '').replace(/_/g, ' ').toLowerCase();

/**
 * The activity log and audit trail, as a timeline.
 *
 * When campaignId is provided, loads campaign-specific activity events and
 * attaches a live SSE listener to stream events dynamically into the timeline.
 */
export default function ActivityLog({ campaignId = null, embedded = false }) {
  const [filter, setFilter] = useState('');
  const [liveEvents, setLiveEvents] = useState([]);

  // Fetch persisted events
  const activity = useAsync(
    async () => {
      if (campaignId) {
        return await listCampaignActivity(campaignId, { eventType: filter || undefined, pageSize: 100 });
      }
      return await listAuditLog({ event: filter || undefined, limit: 200 });
    },
    [filter, campaignId],
    { pollMs: campaignId ? 0 : 15000 }
  );

  // Clear prepended live events when filter or campaign changes
  useEffect(() => {
    setLiveEvents([]);
  }, [filter, campaignId]);

  // Live SSE listener for real-time stream when viewing a campaign
  const handleLiveEvent = useCallback(
    (evt) => {
      if (!evt || !evt.event_type) return;
      if (evt.event_type === 'PROGRESS_UPDATED' || evt.event_type === 'WORKER_STATUS') return;
      if (filter && evt.event_type !== filter) return;

      setLiveEvents((prev) => {
        // Prevent duplicates
        if (prev.some((e) => e.event_id === evt.event_id)) return prev;
        return [evt, ...prev];
      });
    },
    [filter]
  );

  const { isConnected } = useCampaignEvents(campaignId, { onEvent: handleLiveEvent });

  const persistedItems = activity.data?.items || [];
  // Merge live prepended events avoiding duplicate IDs
  const combinedItems = [
    ...liveEvents,
    ...persistedItems.filter((p) => !liveEvents.some((l) => (l.event_id || l.audit_id) === (p.event_id || p.audit_id)))
  ];

  const filterList = campaignId ? CAMPAIGN_FILTERS : GLOBAL_FILTERS;

  const body = (
    <Card
      title={embedded ? 'Activity' : undefined}
      actions={
        <div className="ws-toolbar-right">
          {campaignId && isConnected && (
            <span className="ws-badge ws-badge-sm ws-badge-brand" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Radio size={12} className="ws-spin-pulse" /> Live Stream
            </span>
          )}
          <div className="ws-filter-row">
            {filterList.map((option) => (
              <button
                key={option.key || 'all'}
                className={`ws-filter ${filter === option.key ? 'active' : ''}`}
                onClick={() => setFilter(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button className="ws-btn ws-btn-ghost ws-btn-sm" onClick={activity.reload}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      }
    >
      {activity.loading && !activity.data ? (
        <LoadingState label="Loading activity…" />
      ) : activity.error && !activity.data ? (
        <ErrorState error={activity.error} onRetry={activity.reload} />
      ) : combinedItems.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="Nothing recorded yet"
          description={
            filter
              ? 'No events of this kind. Try a different filter.'
              : 'Lifecycle updates, message transmissions, and worker actions appear here.'
          }
        />
      ) : (
        <Timeline
          items={combinedItems.map((event) => {
            const eventKey = event.event_type || event.event || 'EVENT';
            return {
              id: event.event_id || event.audit_id || `${eventKey}-${event.timestamp || event.at}`,
              tone: TONES[eventKey] || 'neutral',
              title: title(eventKey),
              at: formatTime(event.timestamp || event.at),
              detail: event.details || event.detail,
              meta: (
                <>
                  {event.recipient ? <span className="ws-tag">{event.recipient}</span> : null}
                  {event.campaign_id && !campaignId ? (
                    <span className="ws-tag ws-mono">{event.campaign_id}</span>
                  ) : null}
                  {event.job_id ? <span className="ws-tag ws-mono">{event.job_id}</span> : null}
                  {event.worker_id ? <span className="ws-tag">{event.worker_id}</span> : null}
                  {event.integration ? <span className="ws-tag">{event.integration}</span> : null}
                  {event.outcome ? <span className="ws-tag">{event.outcome}</span> : null}
                </>
              )
            };
          })}
        />
      )}
    </Card>
  );

  if (embedded) return body;

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Activity"
        title="Activity log"
        description="Every campaign lifecycle event, transmission outcome, and permission decision in real time."
      />
      {body}
    </div>
  );
}

import React, { useState } from 'react';
import { Ban, Pause, Play, RefreshCw, Radio } from 'lucide-react';
import { useAction, useAsync } from '../../hooks/useAsync';
import { useCampaignEvents } from '../../hooks/useCampaignEvents';
import {
  cancelCampaign,
  getCampaignProgress,
  listCampaignJobs,
  pauseCampaign,
  resumeCampaign
} from '../../services/api';
import { Card, ProgressBar, StatCard, StatusPill, Toolbar, formatTime } from '../ui/Primitives';
import { EmptyState, ErrorState, InlineSpinner, LoadingState } from '../ui/States';

const JOB_FILTERS = [
  { key: '', label: 'All' },
  { key: 'PENDING', label: 'Pending' },
  { key: 'GENERATING', label: 'Generating' },
  { key: 'READY', label: 'Ready' },
  { key: 'SENT', label: 'Sent' },
  { key: 'FAILED', label: 'Failed' },
  { key: 'RETRY_PENDING', label: 'Retrying' },
  { key: 'SKIPPED', label: 'Skipped' }
];

const LIVE_STATUSES = new Set(['RUNNING']);

/**
 * Live execution view: campaign totals, progress, controls and the per-recipient
 * job list.
 *
 * Automatically connects to Server-Sent Events (SSE) for real-time progress,
 * worker telemetry, and instant job state updates without page refresh.
 */
export default function CampaignExecution({ campaign, campaignId, onCampaignChange }) {
  const [filter, setFilter] = useState('');
  const isLive = LIVE_STATUSES.has(campaign?.status);

  const progress = useAsync(() => getCampaignProgress(campaignId), [campaignId], {
    pollMs: isLive ? 10000 : null
  });
  const jobs = useAsync(() => listCampaignJobs(campaignId, { status: filter, pageSize: 100 }), [
    campaignId,
    filter
  ], { pollMs: isLive ? 12000 : null });

  // Real-time EventSource connection
  const { isConnected, liveProgress, workerStatus, lastEvent } = useCampaignEvents(campaignId, {
    onEvent: (event) => {
      // Reload jobs when new sent/failed/retry events arrive
      if (['MESSAGE_SENT', 'MESSAGE_FAILED', 'MESSAGE_RETRIED', 'DRY_RUN_COMPLETED', 'CAMPAIGN_COMPLETED'].includes(event.event_type)) {
        jobs.reload();
      }
      if (event.event_type === 'CAMPAIGN_COMPLETED') {
        onCampaignChange?.();
      }
    }
  });

  const refreshAll = () => {
    progress.reload();
    jobs.reload();
    onCampaignChange?.();
  };

  const control = useAction(async (action) => {
    if (action === 'pause') await pauseCampaign(campaignId);
    if (action === 'resume') await resumeCampaign(campaignId);
    if (action === 'cancel') await cancelCampaign(campaignId);
    refreshAll();
  });

  if (progress.loading && !progress.data && !liveProgress) {
    return <LoadingState label="Loading campaign progress…" />;
  }
  if (progress.error && !progress.data && !liveProgress) {
    return <ErrorState error={progress.error} onRetry={progress.reload} />;
  }

  // Merge live SSE progress with initial fetched progress
  const p = { ...(progress.data || {}), ...(liveProgress || {}) };
  const status = p.status || campaign?.status;
  const replies = campaign?.replied ?? 0;

  return (
    <div className="ws-execution">
      <Toolbar>
        <div className="ws-toolbar-left">
          <StatusPill status={status} />
          {p.dry_run ? <span className="ws-tag">dry run</span> : null}
          {isConnected ? (
            <span className="ws-tag" style={{ color: 'var(--ws-success, #10b981)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Radio size={11} className="ws-pulse" /> Live
            </span>
          ) : null}
          {workerStatus && workerStatus !== 'idle' ? (
            <span className="ws-tag">Worker: {workerStatus}</span>
          ) : null}
          {progress.refreshing ? <InlineSpinner /> : null}
        </div>
        <div className="ws-toolbar-right">
          <button className="ws-btn ws-btn-ghost ws-btn-sm" onClick={refreshAll}>
            <RefreshCw size={13} /> Refresh
          </button>
          <button
            className="ws-btn ws-btn-ghost ws-btn-sm"
            onClick={() => control.execute('pause')}
            disabled={control.pending || status !== 'RUNNING'}
          >
            <Pause size={13} /> Pause
          </button>
          <button
            className="ws-btn ws-btn-ghost ws-btn-sm"
            onClick={() => control.execute('resume')}
            disabled={control.pending || !['PAUSED', 'READY'].includes(status)}
          >
            <Play size={13} /> Resume
          </button>
          <button
            className="ws-btn ws-btn-danger ws-btn-sm"
            onClick={() => control.execute('cancel')}
            disabled={control.pending || ['CANCELLED', 'COMPLETED'].includes(status)}
          >
            <Ban size={13} /> Cancel
          </button>
        </div>
      </Toolbar>

      {control.error ? <ErrorState error={control.error} compact /> : null}

      <ProgressBar
        value={p.percent_complete || 0}
        label={`${(p.completed || 0).toLocaleString()} of ${(p.total || 0).toLocaleString()} recipients processed`}
        sublabel={`${(p.percent_complete || 0).toFixed(1)}%`}
        tone={status === 'FAILED' ? 'danger' : status === 'PAUSED' ? 'warn' : 'brand'}
      />

      <div className="ws-grid ws-grid-8">
        <StatCard label="Total" value={p.total || 0} />
        <StatCard label="Pending" value={(p.pending || 0) + (p.retry_pending || 0)} tone="muted" />
        <StatCard label="Generating" value={(p.generating || 0) + (p.ready || 0)} tone="brand" />
        <StatCard label="Sent" value={p.sent || 0} tone="success" />
        <StatCard label="Failed" value={p.failed || 0} tone={p.failed ? 'danger' : 'neutral'} />
        <StatCard label="Skipped" value={p.skipped || 0} tone="muted" />
        <StatCard label="Replies" value={replies} hint={replies ? undefined : 'not tracked yet'} />
        <StatCard label="Progress" value={`${Math.round(p.percent_complete || 0)}%`} tone="brand" />
      </div>

      {p.requires_manual_review ? (
        <div className="ws-note ws-note-warn">
          <div>
            <strong>{p.requires_manual_review} job(s) need a human decision.</strong>
            <p>
              A worker stopped while these sends were in flight. They were not retried
              automatically, because the provider may already have accepted them and a
              retry could deliver twice.
            </p>
          </div>
        </div>
      ) : null}

      <Card
        title="Recipients"
        subtitle="One row per recipient action"
        actions={
          <div className="ws-filter-row">
            {JOB_FILTERS.map((option) => (
              <button
                key={option.key || 'all'}
                className={`ws-filter ${filter === option.key ? 'active' : ''}`}
                onClick={() => setFilter(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
        padded={false}
      >
        <JobTable jobs={jobs} filter={filter} />
      </Card>
    </div>
  );
}

function JobTable({ jobs, filter }) {
  if (jobs.loading && !jobs.data) return <LoadingState label="Loading recipients…" />;
  if (jobs.error && !jobs.data) return <ErrorState error={jobs.error} onRetry={jobs.reload} />;

  const items = jobs.data?.items || [];
  if (items.length === 0) {
    return (
      <EmptyState
        title={filter ? `No ${filter.toLowerCase().replace('_', ' ')} recipients` : 'No recipients yet'}
        description={
          filter
            ? 'Try a different filter.'
            : 'Jobs appear here as soon as the campaign is started.'
        }
      />
    );
  }

  return (
    <div className="ws-table-scroll">
      <table className="ws-table">
        <thead>
          <tr>
            <th>Recipient</th>
            <th>Company</th>
            <th>Status</th>
            <th>Subject</th>
            <th>Timestamp</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {items.map((job) => (
            <tr key={job.id}>
              <td>
                <div className="ws-cell-main">{job.recipient_name || job.to_email || job.contact_id}</div>
                <div className="ws-cell-sub">
                  {job.recipient_name ? job.to_email : null}
                  {job.attempt_count > 1 ? ` · attempt ${job.attempt_count}` : ''}
                </div>
              </td>
              <td className="ws-cell-muted">{job.recipient_company || '—'}</td>
              <td>
                <StatusPill status={job.status} />
                {job.provider_status === 'DRY_RUN' ? <span className="ws-tag">preview</span> : null}
              </td>
              <td className="ws-cell-subject" title={job.generated_subject || ''}>
                {job.generated_subject || <span className="ws-muted">not generated yet</span>}
              </td>
              <td className="ws-cell-muted">
                {formatTime(job.sent_at || job.completed_at || job.updated_at)}
              </td>
              <td>
                {job.status === 'FAILED' || job.failure_reason ? (
                  <span className="ws-error-text" title={job.failure_reason || ''}>
                    {job.failure_reason || 'Failed'}
                  </span>
                ) : job.message_id ? (
                  <span className="ws-mono ws-muted" title={job.message_id}>
                    {job.message_id}
                  </span>
                ) : (
                  <span className="ws-muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

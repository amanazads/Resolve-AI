import React from 'react';
import { AlertTriangle, Inbox, Loader2, PlugZap, RefreshCw } from 'lucide-react';

/*
 * The four states every screen has to handle, in one place so they look and
 * behave the same everywhere: loading, empty, error, and the one specific to
 * this product -- an integration that exists but is not connected.
 */

export function LoadingState({ label = 'Loading…', rows = 3 }) {
  return (
    <div className="ws-state ws-state-loading" role="status" aria-live="polite">
      <Loader2 size={18} className="ws-spin" />
      <span>{label}</span>
      <div className="ws-skeleton-stack" aria-hidden="true">
        {Array.from({ length: rows }).map((_, idx) => (
          <div key={idx} className="ws-skeleton" style={{ width: `${100 - idx * 12}%` }} />
        ))}
      </div>
    </div>
  );
}

export function InlineSpinner({ label }) {
  return (
    <span className="ws-inline-spinner">
      <Loader2 size={14} className="ws-spin" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, description, action }) {
  return (
    <div className="ws-state ws-state-empty">
      <div className="ws-state-icon">
        <Icon size={20} />
      </div>
      <h3>{title}</h3>
      {description ? <p>{description}</p> : null}
      {action ? <div className="ws-state-action">{action}</div> : null}
    </div>
  );
}

/**
 * Renders an error from `toApiError`. Permission denials carry `reasons` and a
 * `howToFix`, and showing them is the difference between "403" and an error the
 * user can actually resolve.
 */
export function ErrorState({ error, onRetry, compact = false }) {
  if (!error) return null;
  const reasons = error.reasons || [];

  return (
    <div className={`ws-state ws-state-error ${compact ? 'compact' : ''}`} role="alert">
      <div className="ws-state-icon danger">
        <AlertTriangle size={compact ? 15 : 20} />
      </div>
      <div className="ws-error-body">
        <h3>{error.code === 'permission_denied' ? 'Not authorized' : 'Something went wrong'}</h3>
        <p>{error.message}</p>
        {reasons.length > 0 && (
          <ul className="ws-reason-list">
            {reasons.map((reason, idx) => (
              <li key={idx}>{reason}</li>
            ))}
          </ul>
        )}
        {error.howToFix ? <p className="ws-hint">{error.howToFix}</p> : null}
        {onRetry ? (
          <button className="ws-btn ws-btn-ghost" onClick={onRetry}>
            <RefreshCw size={13} /> Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A connected-integration prerequisite that is not met. Distinct from an error:
 * nothing has gone wrong, the user simply has a step left to take.
 */
export function DisconnectedState({ name, detail, onConnect, connecting }) {
  return (
    <div className="ws-state ws-state-disconnected">
      <div className="ws-state-icon warn">
        <PlugZap size={20} />
      </div>
      <h3>{name} is not connected</h3>
      <p>{detail || `Connect ${name} before this campaign can send anything.`}</p>
      {onConnect ? (
        <button className="ws-btn ws-btn-primary" onClick={onConnect} disabled={connecting}>
          {connecting ? <InlineSpinner label={`Connecting ${name}…`} /> : `Connect ${name}`}
        </button>
      ) : null}
    </div>
  );
}

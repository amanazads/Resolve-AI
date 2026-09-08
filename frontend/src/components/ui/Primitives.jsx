import React from 'react';
import { ChevronRight } from 'lucide-react';

/* Building blocks shared by every workspace screen. */

export function Card({ title, subtitle, actions, children, className = '', padded = true }) {
  return (
    <section className={`ws-card ${className}`}>
      {(title || actions) && (
        <header className="ws-card-head">
          <div>
            {title ? <h2 className="ws-card-title">{title}</h2> : null}
            {subtitle ? <p className="ws-card-subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="ws-card-actions">{actions}</div> : null}
        </header>
      )}
      <div className={padded ? 'ws-card-body' : ''}>{children}</div>
    </section>
  );
}

export function PageHeader({ eyebrow, title, description, actions, backTo, onBack }) {
  return (
    <header className="ws-page-head">
      <div className="ws-page-head-text">
        {backTo ? (
          <button className="ws-crumb" onClick={onBack}>
            {backTo} <ChevronRight size={12} />
          </button>
        ) : null}
        {eyebrow ? <span className="ws-eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="ws-page-head-actions">{actions}</div> : null}
    </header>
  );
}

const TONES = {
  neutral: 'neutral',
  brand: 'brand',
  success: 'success',
  warn: 'warn',
  danger: 'danger',
  muted: 'muted'
};

export function StatCard({ label, value, hint, tone = 'neutral', icon: Icon }) {
  return (
    <div className={`ws-stat ws-tone-${TONES[tone] || 'neutral'}`}>
      <div className="ws-stat-label">
        {Icon ? <Icon size={13} /> : null}
        <span>{label}</span>
      </div>
      <div className="ws-stat-value">{value}</div>
      {hint ? <div className="ws-stat-hint">{hint}</div> : null}
    </div>
  );
}

/** Job and campaign statuses share a vocabulary; this is the single mapping. */
const STATUS_TONES = {
  SENT: 'success',
  COMPLETED: 'success',
  CONNECTED: 'success',
  GRANTED: 'success',
  RUNNING: 'brand',
  GENERATING: 'brand',
  SENDING: 'brand',
  READY: 'brand',
  PENDING: 'muted',
  DRAFT: 'muted',
  SKIPPED: 'muted',
  CANCELLED: 'muted',
  DISCONNECTED: 'muted',
  PAUSED: 'warn',
  RETRY_PENDING: 'warn',
  NEEDS_REAUTH: 'warn',
  RATE_LIMITED: 'warn',
  PENDING_HUMAN_APPROVAL: 'warn',
  NOT_CONFIGURED: 'warn',
  FAILED: 'danger',
  UNAUTHORIZED: 'danger',
  DENIED: 'danger',
  BLOCKED: 'danger'
};

export function StatusPill({ status, children, title }) {
  const key = String(status || '').toUpperCase();
  const tone = STATUS_TONES[key] || 'neutral';
  return (
    <span className={`ws-pill ws-tone-${tone}`} title={title || key}>
      <span className="ws-pill-dot" />
      {children || key.replace(/_/g, ' ')}
    </span>
  );
}

export function ProgressBar({ value = 0, tone = 'brand', label, sublabel }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="ws-progress-wrap">
      {(label || sublabel) && (
        <div className="ws-progress-meta">
          <span>{label}</span>
          <span className="ws-progress-sub">{sublabel}</span>
        </div>
      )}
      <div
        className="ws-progress"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`ws-progress-fill ws-tone-${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Field({ label, hint, children, htmlFor }) {
  return (
    <label className="ws-field" htmlFor={htmlFor}>
      <span className="ws-field-label">{label}</span>
      {children}
      {hint ? <span className="ws-field-hint">{hint}</span> : null}
    </label>
  );
}

export function KeyValue({ items }) {
  return (
    <dl className="ws-kv">
      {items
        .filter((item) => item && item.value !== undefined && item.value !== null && item.value !== '')
        .map((item) => (
          <div className="ws-kv-row" key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
    </dl>
  );
}

export function Timeline({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <ol className="ws-timeline">
      {items.map((item, idx) => (
        <li key={item.id || idx} className={`ws-timeline-item ws-tone-${item.tone || 'neutral'}`}>
          <span className="ws-timeline-marker" />
          <div className="ws-timeline-body">
            <div className="ws-timeline-head">
              <strong>{item.title}</strong>
              {item.at ? <time>{item.at}</time> : null}
            </div>
            {item.detail ? <p>{item.detail}</p> : null}
            {item.meta ? <div className="ws-timeline-meta">{item.meta}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Toolbar({ children }) {
  return <div className="ws-toolbar">{children}</div>;
}

/** Short, locale-aware timestamp. Invalid or absent values render as an em dash. */
export function formatTime(value, { withDate = true } = {}) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    month: withDate ? 'short' : undefined,
    day: withDate ? 'numeric' : undefined,
    hour: '2-digit',
    minute: '2-digit'
  });
}

export function relativeTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

import React from 'react';
import {
  ArrowRight,
  CheckCircle2,
  Mail,
  Plug,
  Send,
  Sparkles,
  Upload,
  Users
} from 'lucide-react';
import { navigate } from '../../router';
import { useAsync } from '../../hooks/useAsync';
import {
  getGmailStatus,
  listAuditLog,
  listCampaigns,
  listDatasets
} from '../../services/api';
import { Card, PageHeader, StatCard, StatusPill, Timeline, formatTime, relativeTime } from '../ui/Primitives';
import { ErrorState, LoadingState } from '../ui/States';
import ObjectiveComposer from './ObjectiveComposer';

const EVENT_TONES = {
  MESSAGE_SENT: 'success',
  CAMPAIGN_STARTED: 'brand',
  PERMISSION_GRANTED: 'success',
  PERMISSION_REVOKED: 'warn',
  PERMISSION_DENIED: 'danger',
  MESSAGE_FAILED: 'danger',
  INTEGRATION_CONNECTED: 'success',
  INTEGRATION_DISCONNECTED: 'warn'
};

/**
 * The workspace landing screen.
 *
 * Its job is to get someone from nothing to a running campaign: upload contacts,
 * connect a mailbox, then describe the objective in their own words. The three
 * setup steps show their real state, so it is obvious what is still missing.
 */
export default function Dashboard() {
  const datasets = useAsync(() => listDatasets(1, 50), []);
  const campaigns = useAsync(() => listCampaigns(), []);
  const gmail = useAsync(() => getGmailStatus(), []);
  const audit = useAsync(() => listAuditLog({ limit: 8 }), [], { pollMs: 20000 });

  const loading = datasets.loading || campaigns.loading;
  const datasetList = datasets.data?.datasets || [];
  const campaignList = campaigns.data || [];
  const contactCount = datasetList.reduce(
    (sum, d) => sum + (d?.statistics?.valid_contacts || 0),
    0
  );
  const running = campaignList.filter((c) => c.status === 'RUNNING').length;
  const sent = campaignList.reduce((sum, c) => sum + (c.sent || 0), 0);

  const gmailConnected = Boolean(gmail.data?.connected);
  const hasContacts = contactCount > 0;

  const steps = [
    {
      key: 'contacts',
      icon: Upload,
      title: 'Upload contacts',
      detail: hasContacts
        ? `${contactCount.toLocaleString()} valid contacts across ${datasetList.length} dataset${datasetList.length === 1 ? '' : 's'}`
        : 'Import a CSV or XLSX of the people you want to reach.',
      done: hasContacts,
      cta: hasContacts ? 'Manage contacts' : 'Upload a file',
      onClick: () => navigate('/datasets')
    },
    {
      key: 'gmail',
      icon: Mail,
      title: 'Connect Gmail',
      detail: gmail.loading
        ? 'Checking connection…'
        : gmailConnected
          ? `Connected as ${gmail.data.email_address || 'your mailbox'}`
          : gmail.data?.detail || 'Authorize Gmail over OAuth so campaigns can send.',
      done: gmailConnected,
      cta: gmailConnected ? 'Manage' : 'Connect Gmail',
      onClick: () => navigate('/integrations')
    },
    {
      key: 'integrations',
      icon: Plug,
      title: 'Connect other integrations',
      detail: 'LinkedIn outreach and additional providers appear here as they are enabled.',
      done: false,
      cta: 'View integrations',
      onClick: () => navigate('/integrations')
    }
  ];

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Workspace"
        title="What should Resolve AI do next?"
        description="Describe an objective in plain language. The agent plans it, shows you the plan, and waits for your approval before anything is sent."
        actions={
          <button className="ws-btn ws-btn-ghost" onClick={() => navigate('/campaigns')}>
            <Send size={14} /> All campaigns
          </button>
        }
      />

      <ObjectiveComposer datasets={datasetList} datasetsLoading={datasets.loading} />

      <div className="ws-grid ws-grid-3">
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <button key={step.key} className={`ws-setup-card ${step.done ? 'done' : ''}`} onClick={step.onClick}>
              <div className="ws-setup-icon">
                {step.done ? <CheckCircle2 size={17} /> : <Icon size={17} />}
              </div>
              <div className="ws-setup-text">
                <strong>{step.title}</strong>
                <span>{step.detail}</span>
              </div>
              <span className="ws-setup-cta">
                {step.cta} <ArrowRight size={13} />
              </span>
            </button>
          );
        })}
      </div>

      {loading ? (
        <LoadingState label="Loading your workspace…" />
      ) : datasets.error || campaigns.error ? (
        <ErrorState
          error={datasets.error || campaigns.error}
          onRetry={() => {
            datasets.reload();
            campaigns.reload();
          }}
        />
      ) : (
        <>
          <div className="ws-grid ws-grid-4">
            <StatCard label="Contacts" value={contactCount.toLocaleString()} icon={Users} />
            <StatCard label="Campaigns" value={campaignList.length} icon={Send} />
            <StatCard label="Running now" value={running} tone={running ? 'brand' : 'neutral'} />
            <StatCard label="Messages sent" value={sent.toLocaleString()} tone={sent ? 'success' : 'neutral'} />
          </div>

          <div className="ws-grid ws-grid-2">
            <Card
              title="Recent campaigns"
              actions={
                <button className="ws-btn ws-btn-ghost ws-btn-sm" onClick={() => navigate('/campaigns')}>
                  View all
                </button>
              }
            >
              {campaignList.length === 0 ? (
                <div className="ws-inline-empty">
                  <Sparkles size={15} />
                  <span>No campaigns yet. Describe an objective above to create your first one.</span>
                </div>
              ) : (
                <ul className="ws-mini-list">
                  {campaignList.slice(0, 5).map((campaign) => (
                    <li key={campaign.campaign_id}>
                      <button
                        className="ws-mini-row"
                        onClick={() => navigate(`/campaigns/${campaign.campaign_id}`)}
                      >
                        <div className="ws-mini-main">
                          <strong>{campaign.name}</strong>
                          <span>{campaign.objective}</span>
                        </div>
                        <div className="ws-mini-side">
                          <StatusPill status={campaign.status} />
                          <span className="ws-muted">{relativeTime(campaign.updated_at)}</span>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card
              title="Activity"
              subtitle="Every permission decision and message outcome"
              actions={
                <button className="ws-btn ws-btn-ghost ws-btn-sm" onClick={() => navigate('/activity')}>
                  Full log
                </button>
              }
            >
              {audit.loading ? (
                <LoadingState label="Loading activity…" rows={2} />
              ) : audit.error ? (
                <ErrorState error={audit.error} onRetry={audit.reload} compact />
              ) : (audit.data?.items || []).length === 0 ? (
                <div className="ws-inline-empty">
                  <span>Nothing has happened yet.</span>
                </div>
              ) : (
                <Timeline
                  items={(audit.data.items || []).map((event) => ({
                    id: event.audit_id,
                    tone: EVENT_TONES[event.event] || 'neutral',
                    title: event.event.replace(/_/g, ' ').toLowerCase(),
                    at: formatTime(event.at),
                    detail: event.detail
                  }))}
                />
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

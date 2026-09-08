import React, { useState } from 'react';
import { RefreshCw, Search, Sparkles } from 'lucide-react';
import { navigate } from '../../router';
import { useAsync } from '../../hooks/useAsync';
import { listCampaigns } from '../../services/api';
import { Card, PageHeader, ProgressBar, StatusPill, relativeTime } from '../ui/Primitives';
import { EmptyState, ErrorState, LoadingState } from '../ui/States';

const STATUS_TABS = ['ALL', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED'];

export default function Campaigns() {
  const [status, setStatus] = useState('ALL');
  const [query, setQuery] = useState('');
  const campaigns = useAsync(() => listCampaigns(), [], { pollMs: 15000 });

  const all = campaigns.data || [];
  const filtered = all.filter((campaign) => {
    if (status !== 'ALL' && campaign.status !== status) return false;
    if (!query.trim()) return true;
    const haystack = `${campaign.name} ${campaign.objective} ${campaign.campaign_id}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Campaigns"
        title="Campaigns"
        description="Every campaign the agent has planned or run."
        actions={
          <>
            <button className="ws-btn ws-btn-ghost" onClick={campaigns.reload}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button className="ws-btn ws-btn-primary" onClick={() => navigate('/create')}>
              <Sparkles size={14} /> New campaign
            </button>
          </>
        }
      />

      <div className="ws-toolbar">
        <div className="ws-filter-row">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab}
              className={`ws-filter ${status === tab ? 'active' : ''}`}
              onClick={() => setStatus(tab)}
            >
              {tab === 'ALL' ? 'All' : tab.toLowerCase()}
            </button>
          ))}
        </div>
        <div className="ws-search">
          <Search size={14} />
          <input
            value={query}
            placeholder="Search campaigns"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      {campaigns.loading && !campaigns.data ? (
        <LoadingState label="Loading campaigns…" />
      ) : campaigns.error && !campaigns.data ? (
        <ErrorState error={campaigns.error} onRetry={campaigns.reload} />
      ) : all.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="No campaigns yet"
          description="Describe an objective and the agent will plan your first campaign."
          action={
            <button className="ws-btn ws-btn-primary" onClick={() => navigate('/create')}>
              Create a campaign
            </button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="Nothing matches those filters"
          description="Try a different status or clear the search."
          action={
            <button
              className="ws-btn ws-btn-ghost"
              onClick={() => {
                setStatus('ALL');
                setQuery('');
              }}
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <div className="ws-campaign-list">
          {filtered.map((campaign) => {
            const total = campaign.total_contacts || 0;
            const done = (campaign.sent || 0) + (campaign.failed || 0);
            const pct = total ? (done / total) * 100 : 0;
            return (
              <Card key={campaign.campaign_id} className="ws-campaign-card">
                <button
                  className="ws-campaign-main"
                  onClick={() => navigate(`/campaigns/${campaign.campaign_id}`)}
                >
                  <div className="ws-campaign-head">
                    <div>
                      <strong>{campaign.name}</strong>
                      <p>{campaign.objective}</p>
                    </div>
                    <StatusPill status={campaign.status} />
                  </div>

                  <div className="ws-campaign-stats">
                    <span><b>{total.toLocaleString()}</b> recipients</span>
                    <span className="ws-tone-success"><b>{campaign.sent || 0}</b> sent</span>
                    <span className={campaign.failed ? 'ws-tone-danger' : ''}>
                      <b>{campaign.failed || 0}</b> failed
                    </span>
                    <span className="ws-muted">updated {relativeTime(campaign.updated_at)}</span>
                  </div>

                  <ProgressBar value={pct} tone={campaign.status === 'FAILED' ? 'danger' : 'brand'} />
                </button>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

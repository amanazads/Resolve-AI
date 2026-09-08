import React from 'react';
import { Activity, Info, Users } from 'lucide-react';
import { navigate } from '../../router';
import { useAsync } from '../../hooks/useAsync';
import { getCampaign } from '../../services/api';
import { Card, KeyValue, PageHeader, StatusPill, formatTime } from '../ui/Primitives';
import { ErrorState, LoadingState } from '../ui/States';
import CampaignExecution from './CampaignExecution';
import ActivityLog from './ActivityLog';

const TABS = [
  { key: 'jobs', label: 'Execution', icon: Users },
  { key: 'overview', label: 'Overview', icon: Info },
  { key: 'activity', label: 'Activity', icon: Activity }
];

/**
 * One campaign: its plan, its live execution and its audit trail.
 */
export default function CampaignDetails({ campaignId, tab }) {
  const active = TABS.some((t) => t.key === tab) ? tab : 'jobs';
  const campaign = useAsync(() => getCampaign(campaignId), [campaignId]);

  if (campaign.loading && !campaign.data) return <LoadingState label="Loading campaign…" />;
  if (campaign.error && !campaign.data) {
    return (
      <div className="ws-screen">
        <PageHeader title="Campaign" backTo="Campaigns" onBack={() => navigate('/campaigns')} />
        <ErrorState error={campaign.error} onRetry={campaign.reload} />
      </div>
    );
  }

  const data = campaign.data || {};

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow={data.campaign_id}
        title={data.name || 'Campaign'}
        description={data.objective}
        backTo="Campaigns"
        onBack={() => navigate('/campaigns')}
        actions={<StatusPill status={data.status} />}
      />

      <div className="ws-tabs">
        {TABS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.key}
              className={`ws-tab ${active === item.key ? 'active' : ''}`}
              onClick={() => navigate(`/campaigns/${campaignId}/${item.key}`)}
            >
              <Icon size={14} /> {item.label}
            </button>
          );
        })}
      </div>

      {active === 'jobs' ? (
        <CampaignExecution
          campaign={data}
          campaignId={campaignId}
          onCampaignChange={campaign.reload}
        />
      ) : null}

      {active === 'overview' ? (
        <div className="ws-grid ws-grid-2">
          <Card title="Campaign">
            <KeyValue
              items={[
                { label: 'Objective', value: data.objective },
                { label: 'Audience', value: (data.audience || []).join(', ') || 'All contacts' },
                { label: 'Channel', value: data.communication_channel },
                { label: 'Dataset', value: data.dataset_id },
                { label: 'Message strategy', value: data.message_strategy },
                {
                  label: 'Personalization',
                  value: (data.personalization_fields || []).join(', ')
                },
                { label: 'Created', value: formatTime(data.created_at) },
                { label: 'Updated', value: formatTime(data.updated_at) }
              ]}
            />
          </Card>

          <Card title="Plan">
            {data.plan ? (
              <KeyValue
                items={[
                  { label: 'Type', value: data.plan.campaign_type },
                  { label: 'Tone', value: data.plan.tone },
                  { label: 'Subject line', value: data.plan.suggested_subject_line },
                  { label: 'Call to action', value: data.plan.call_to_action },
                  {
                    label: 'Steps',
                    value: (data.plan.steps || [])
                      .map((s) => s.action?.replace(/_/g, ' ').toLowerCase())
                      .join(' → ')
                  }
                ]}
              />
            ) : (
              <p className="ws-muted">No plan was stored for this campaign.</p>
            )}
          </Card>
        </div>
      ) : null}

      {active === 'activity' ? <ActivityLog campaignId={campaignId} embedded /> : null}
    </div>
  );
}

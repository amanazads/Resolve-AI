import React, { useEffect, useState } from 'react';
import { Eye, Sparkles, Upload } from 'lucide-react';
import { navigate } from '../../router';
import { useAction, useAsync } from '../../hooks/useAsync';
import {
  approveAndRunAutomation,
  dryRunAutomation,
  getGmailStatus,
  listDatasets,
  planAutomation
} from '../../services/api';
import { Card, Field, PageHeader, StatusPill } from '../ui/Primitives';
import { DisconnectedState, ErrorState, InlineSpinner, LoadingState } from '../ui/States';
import CampaignPlan from './CampaignPlan';
import { DATASET_KEY, OBJECTIVE_KEY } from './ObjectiveComposer';

const readDraft = (key) => {
  try {
    return sessionStorage.getItem(key) || '';
  } catch {
    return '';
  }
};

/**
 * Objective -> plan -> approval.
 *
 * The three buttons map onto three different authorization scopes sent to the
 * agent, which is what makes the distinction real rather than cosmetic:
 *
 *   Plan it       no send permission  -> the run stops before creating anything
 *   Dry run       dry-run permission  -> messages are generated, nothing is sent
 *   Start         explicit approval   -> the campaign runs
 */
export default function CreateCampaign({ initialDatasetId }) {
  const [objective, setObjective] = useState(() => readDraft(OBJECTIVE_KEY));
  const [datasetId, setDatasetId] = useState(() => initialDatasetId || readDraft(DATASET_KEY));
  const [run, setRun] = useState(null);
  const [dryRunResult, setDryRunResult] = useState(null);

  const datasets = useAsync(() => listDatasets(1, 50), []);
  const gmail = useAsync(() => getGmailStatus(), []);

  const datasetList = datasets.data?.datasets || [];
  const gmailConnected = Boolean(gmail.data?.connected);

  const plan = useAction(async () => {
    setDryRunResult(null);
    const result = await planAutomation({ message: objective, datasetId });
    setRun(result);
    return result;
  });

  const dryRun = useAction(async () => {
    const result = await dryRunAutomation({ message: objective, datasetId });
    setDryRunResult(result);
    setRun((prev) => ({ ...(prev || {}), ...result }));
    return result;
  });

  const start = useAction(async () => {
    const recipients = run?.dataset_assessment?.matched_contacts || undefined;
    const result = await approveAndRunAutomation({
      message: objective,
      datasetId,
      maxRecipients: recipients
    });
    if (result?.campaign_id) {
      navigate(`/campaigns/${result.campaign_id}`);
    } else {
      setRun((prev) => ({ ...(prev || {}), ...result }));
    }
    return result;
  });

  // Clear the handoff from the dashboard once it has been consumed.
  useEffect(() => {
    try {
      sessionStorage.removeItem(OBJECTIVE_KEY);
      sessionStorage.removeItem(DATASET_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const blockedReason = (() => {
    if (!run) return null;
    const goal = run.goal_analysis || {};
    const assessment = run.dataset_assessment || {};
    if (goal.is_automation_request === false) {
      return run.response || 'This does not look like a bulk outreach request.';
    }
    if (assessment.sufficient === false) {
      return assessment.reason || 'There are no contacts matching this audience.';
    }
    return null;
  })();

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="New campaign"
        title="Describe what you want to happen"
        description="The agent reads your objective, checks the contacts you actually have, and writes a plan. Nothing is created until you approve it."
        backTo="Dashboard"
        onBack={() => navigate('/dashboard')}
      />

      <Card title="Objective">
        <Field label="What should the agent do?" htmlFor="objective">
          <textarea
            id="objective"
            className="ws-textarea"
            rows={3}
            value={objective}
            placeholder="e.g. Send personalized fundraising emails to investors in this sheet."
            onChange={(e) => setObjective(e.target.value)}
          />
        </Field>

        <div className="ws-row">
          <Field label="Contacts" hint="Leave on automatic to let the agent pick the dataset.">
            {datasets.loading ? (
              <InlineSpinner label="Loading datasets…" />
            ) : datasetList.length === 0 ? (
              <button className="ws-btn ws-btn-ghost" onClick={() => navigate('/datasets')}>
                <Upload size={14} /> Upload contacts first
              </button>
            ) : (
              <select
                className="ws-select"
                value={datasetId || ''}
                onChange={(e) => setDatasetId(e.target.value)}
              >
                <option value="">Automatic</option>
                {datasetList.map((dataset) => (
                  <option key={dataset.dataset_id} value={dataset.dataset_id}>
                    {dataset.filename} ({dataset.statistics?.valid_contacts ?? 0} contacts)
                  </option>
                ))}
              </select>
            )}
          </Field>

          <div className="ws-row-actions">
            <button
              className="ws-btn ws-btn-primary"
              onClick={plan.execute}
              disabled={!objective.trim() || plan.pending}
            >
              {plan.pending ? <InlineSpinner label="Planning…" /> : (<><Sparkles size={14} /> Plan it</>)}
            </button>
          </div>
        </div>

        {plan.error ? <ErrorState error={plan.error} compact onRetry={plan.execute} /> : null}
      </Card>

      {plan.pending && !run ? (
        <LoadingState label="Reading your objective, checking the data and drafting a plan…" />
      ) : null}

      {blockedReason ? (
        <Card title="The agent stopped before planning">
          <div className="ws-note ws-note-warn">
            <div>
              <p>{blockedReason}</p>
              {(run?.dataset_assessment?.data_gaps || []).map((gap, idx) => (
                <p key={idx}>{gap}</p>
              ))}
            </div>
          </div>
          <button className="ws-btn ws-btn-ghost" onClick={() => navigate('/datasets')}>
            <Upload size={14} /> Manage contacts
          </button>
        </Card>
      ) : null}

      {!gmailConnected && !gmail.loading && run && !blockedReason ? (
        <DisconnectedState
          name="Gmail"
          detail={
            gmail.data?.detail ||
            'A campaign can be planned and dry-run without it, but sending needs a connected mailbox.'
          }
          onConnect={() => navigate('/integrations')}
        />
      ) : null}

      {run && !blockedReason ? (
        <>
          <CampaignPlan
            run={run}
            onDryRun={dryRun.execute}
            onStart={start.execute}
            dryRunPending={dryRun.pending}
            startPending={start.pending}
            disabled={!gmailConnected}
            disabledReason="Connect Gmail before starting this campaign. Dry run still works."
          />
          {dryRun.error ? <ErrorState error={dryRun.error} compact /> : null}
          {start.error ? <ErrorState error={start.error} compact /> : null}
        </>
      ) : null}

      {dryRunResult ? (
        <Card
          title="Dry run complete"
          subtitle="Messages were generated and recorded. Nothing was sent."
          actions={
            dryRunResult.campaign_id ? (
              <button
                className="ws-btn ws-btn-ghost ws-btn-sm"
                onClick={() => navigate(`/campaigns/${dryRunResult.campaign_id}/jobs`)}
              >
                <Eye size={13} /> Inspect previews
              </button>
            ) : null
          }
        >
          <div className="ws-dryrun-summary">
            <StatusPill status="SKIPPED">nothing sent</StatusPill>
            <span>{dryRunResult.response}</span>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

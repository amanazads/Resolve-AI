import React from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  ListChecks,
  Mail,
  Plug,
  Rocket,
  Target,
  Users,
  Wand2
} from 'lucide-react';
import { Card, KeyValue, StatusPill } from '../ui/Primitives';
import { InlineSpinner } from '../ui/States';

/**
 * The plan the agent produced, shown for review before anything happens.
 *
 * Everything here comes from the agent run: the goal it understood, the audience
 * it measured, and the steps it intends to take. Nothing on this screen has been
 * executed -- the two buttons at the bottom are the only way past it.
 */
export default function CampaignPlan({
  run,
  onDryRun,
  onStart,
  dryRunPending,
  startPending,
  disabled,
  disabledReason
}) {
  if (!run) return null;

  const goal = run.goal_analysis || {};
  const assessment = run.dataset_assessment || {};
  const plan = run.plan || run.plan_draft || {};
  const validation = run.plan_validation || {};
  const personalization = run.personalization || {};

  const recipients = assessment.matched_contacts ?? plan.estimated_recipients ?? 0;
  const steps = plan.steps || [];
  const sends = steps.some((s) => s.action === 'SEND_EMAIL');
  const requiredIntegrations = sends ? ['Gmail (email delivery)'] : [];
  const estimatedActions = recipients * Math.max(steps.length, 1);

  const blocked = validation.is_valid === false;

  return (
    <div className="ws-plan">
      <Card
        title="Execution plan"
        subtitle="Review what the agent intends to do. Nothing has been created or sent yet."
      >
        <div className="ws-plan-grid">
          <PlanFact icon={Target} label="Goal" value={plan.objective || goal.objective || '—'} wide />
          <PlanFact
            icon={Users}
            label="Audience"
            value={(plan.audience || goal.audience || []).join(', ') || 'Everyone in the dataset'}
          />
          <PlanFact
            icon={Users}
            label="Recipients"
            value={
              <span>
                <strong>{recipients.toLocaleString()}</strong>
                {assessment.dataset_id ? <span className="ws-muted"> from {assessment.dataset_id}</span> : null}
              </span>
            }
          />
          <PlanFact icon={Mail} label="Channel" value={plan.channel || goal.channel || 'EMAIL'} />
          <PlanFact
            icon={Wand2}
            label="Personalization"
            value={
              <>
                <div>{plan.message_strategy || '—'}</div>
                {(personalization.allowed_fields || []).length > 0 ? (
                  <div className="ws-tag-row">
                    {personalization.allowed_fields.map((field) => (
                      <span key={field} className="ws-tag">
                        {field}
                      </span>
                    ))}
                  </div>
                ) : null}
              </>
            }
            wide
          />
          <PlanFact
            icon={ListChecks}
            label="Estimated actions"
            value={`${estimatedActions.toLocaleString()} (${steps.length} step${steps.length === 1 ? '' : 's'} × ${recipients.toLocaleString()} recipients)`}
          />
          <PlanFact
            icon={Plug}
            label="Required integrations"
            value={
              requiredIntegrations.length ? (
                <div className="ws-tag-row">
                  {requiredIntegrations.map((name) => (
                    <span key={name} className="ws-tag">
                      {name}
                    </span>
                  ))}
                </div>
              ) : (
                'None — this plan sends nothing'
              )
            }
          />
        </div>

        <ol className="ws-steps">
          {steps.map((step, idx) => (
            <li key={`${step.action}-${idx}`}>
              <span className="ws-step-index">{step.order ?? idx + 1}</span>
              <div>
                <strong>{step.action.replace(/_/g, ' ').toLowerCase()}</strong>
                {step.description ? <p>{step.description}</p> : null}
              </div>
              {step.requires_authorization ? (
                <StatusPill status="PENDING_HUMAN_APPROVAL">needs approval</StatusPill>
              ) : null}
            </li>
          ))}
        </ol>

        {(validation.warnings || []).length > 0 && (
          <div className="ws-note ws-note-warn">
            <AlertTriangle size={14} />
            <div>
              {validation.warnings.map((warning, idx) => (
                <p key={idx}>{warning}</p>
              ))}
            </div>
          </div>
        )}

        {blocked && (
          <div className="ws-note ws-note-danger">
            <AlertTriangle size={14} />
            <div>
              <strong>This plan was rejected by the safety checks.</strong>
              {(validation.errors || []).map((error, idx) => (
                <p key={idx}>{error}</p>
              ))}
            </div>
          </div>
        )}
      </Card>

      {personalization.sample_subject ? (
        <Card
          title="Sample message"
          subtitle={`Generated for ${personalization.sample_recipient?.email || 'a real contact'} — this is what recipients will receive.`}
        >
          <div className="ws-sample">
            <KeyValue items={[{ label: 'Subject', value: personalization.sample_subject }]} />
            <pre className="ws-sample-body">{personalization.sample_body}</pre>
          </div>
        </Card>
      ) : null}

      <div className="ws-approve-bar">
        <div className="ws-approve-text">
          {disabled ? (
            <span className="ws-warn-text">
              <AlertTriangle size={14} /> {disabledReason}
            </span>
          ) : (
            <span className="ws-muted">
              <CheckCircle2 size={14} /> Approving records a permission scoped to this campaign,
              audience and mailbox — you will not be asked again for each recipient.
            </span>
          )}
        </div>
        <div className="ws-approve-actions">
          <button
            className="ws-btn ws-btn-ghost"
            onClick={onDryRun}
            disabled={blocked || dryRunPending || startPending}
          >
            {dryRunPending ? <InlineSpinner label="Generating…" /> : (<><Eye size={14} /> Dry run</>)}
          </button>
          <button
            className="ws-btn ws-btn-primary"
            onClick={onStart}
            disabled={blocked || disabled || startPending || dryRunPending}
          >
            {startPending ? <InlineSpinner label="Starting…" /> : (<><Rocket size={14} /> Start campaign</>)}
          </button>
        </div>
      </div>
    </div>
  );
}

function PlanFact({ icon: Icon, label, value, wide }) {
  return (
    <div className={`ws-plan-fact ${wide ? 'wide' : ''}`}>
      <div className="ws-plan-fact-label">
        <Icon size={13} />
        <span>{label}</span>
      </div>
      <div className="ws-plan-fact-value">{value}</div>
    </div>
  );
}

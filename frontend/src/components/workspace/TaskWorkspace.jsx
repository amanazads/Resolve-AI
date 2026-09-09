import React, { useState, useEffect, useRef } from 'react';
import {
  Sparkles,
  Paperclip,
  Send,
  CheckCircle2,
  AlertCircle,
  Clock,
  Play,
  FileText,
  X,
  Mail,
  Search,
  HelpCircle,
  ShieldCheck,
  RotateCcw,
  ArrowRight,
  ArrowLeft,
  XCircle,
  Activity,
  Terminal,
  ExternalLink
} from 'lucide-react';
import {
  createTask,
  listTasks,
  getTask,
  sendTaskMessage,
  approveTask,
  cancelTask,
  uploadTaskAttachment
} from '../../services/api';
import { navigate } from '../../router';
import { Card, PageHeader, StatusPill } from '../ui/Primitives';
import { LoadingState, InlineSpinner } from '../ui/States';

export default function TaskWorkspace({ initialTaskId }) {
  const [objective, setObjective] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [dryRun, setDryRun] = useState(false);

  // Active task state
  const [activeTask, setActiveTask] = useState(null);
  const [loading, setLoading] = useState(false);
  const [replyText, setReplyText] = useState('');
  const [replying, setReplying] = useState(false);
  const [approving, setApproving] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState(null);

  const fileInputRef = useRef(null);

  useEffect(() => {
    if (initialTaskId) {
      loadTask(initialTaskId);
    } else {
      setActiveTask(null);
    }
  }, [initialTaskId]);

  const loadTask = async (taskId) => {
    try {
      setLoading(true);
      setError(null);
      const t = await getTask(taskId);
      setActiveTask(t);
    } catch (err) {
      setError(err?.message || 'Failed to load task');
    } finally {
      setLoading(false);
    }
  };

  // Poll active task if in-flight
  useEffect(() => {
    if (!activeTask?.task_id) return;
    const terminalStatuses = ['COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_FOR_USER', 'WAITING_FOR_AUTHORIZATION', 'PAUSED'];
    if (terminalStatuses.includes(activeTask.status)) return;

    const interval = setInterval(async () => {
      try {
        const updated = await getTask(activeTask.task_id);
        setActiveTask(updated);
        if (terminalStatuses.includes(updated.status)) {
          clearInterval(interval);
        }
      } catch (e) {
        console.error('Poll error', e);
      }
    }, 1500);

    return () => clearInterval(interval);
  }, [activeTask?.status, activeTask?.task_id]);

  const handleFileUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;

    setUploadingFile(true);
    try {
      for (const f of files) {
        const uploaded = await uploadTaskAttachment(f);
        setAttachments((prev) => [
          ...prev,
          {
            filename: uploaded.filename,
            content: uploaded.content,
            is_base64: true,
            size_bytes: uploaded.size_bytes
          }
        ]);
      }
    } catch (err) {
      setError('Failed to upload file: ' + (err?.message || ''));
    } finally {
      setUploadingFile(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const removeAttachment = (idx) => {
    setAttachments((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleRunTask = async (e) => {
    if (e) e.preventDefault();
    if (!objective.trim()) return;

    setLoading(true);
    setError(null);
    try {
      const created = await createTask({
        objective: objective.trim(),
        attachments,
        dryRun
      });
      setActiveTask(created);
      setObjective('');
      setAttachments([]);
      navigate(`/tasks/${created.task_id}`);
    } catch (err) {
      setError(err?.message || 'Failed to initiate task');
    } finally {
      setLoading(false);
    }
  };

  const handleSendClarificationReply = async (customReply) => {
    const msg = customReply || replyText;
    if (!msg.trim() || !activeTask?.task_id) return;

    setReplying(true);
    setError(null);
    try {
      const updated = await sendTaskMessage(activeTask.task_id, msg.trim());
      setActiveTask(updated);
      setReplyText('');
    } catch (err) {
      setError(err?.message || 'Failed to submit clarification response');
    } finally {
      setReplying(false);
    }
  };

  const handleApprove = async () => {
    if (!activeTask?.task_id) return;
    setApproving(true);
    setError(null);
    try {
      const updated = await approveTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Failed to approve task execution');
    } finally {
      setApproving(false);
    }
  };

  const handleCancel = async () => {
    if (!activeTask?.task_id) return;
    setCancelling(true);
    setError(null);
    try {
      const updated = await cancelTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Failed to cancel task');
    } finally {
      setCancelling(false);
    }
  };

  const renderStatusBadge = (status) => {
    switch (status) {
      case 'UNDERSTANDING':
      case 'INSPECTING':
      case 'PLANNING':
        return <StatusPill tone="info" label={status} />;
      case 'WAITING_FOR_USER':
        return <StatusPill tone="warning" label="Clarification Needed" />;
      case 'WAITING_FOR_AUTHORIZATION':
        return <StatusPill tone="warning" label="Authorization Required" />;
      case 'RUNNING':
      case 'VERIFYING':
        return <StatusPill tone="info" label="Executing..." />;
      case 'COMPLETED':
        return <StatusPill tone="success" label="Completed" />;
      case 'FAILED':
        return <StatusPill tone="danger" label="Failed" />;
      case 'CANCELLED':
      case 'PAUSED':
        return <StatusPill tone="neutral" label={status} />;
      default:
        return <StatusPill tone="neutral" label={status || 'Unknown'} />;
    }
  };

  // Determine active stage in lifecycle pipeline
  const getStageIndex = (status) => {
    switch (status) {
      case 'UNDERSTANDING':
        return 0;
      case 'INSPECTING':
        return 1;
      case 'WAITING_FOR_USER':
        return 1;
      case 'PLANNING':
        return 2;
      case 'WAITING_FOR_AUTHORIZATION':
        return 3;
      case 'RUNNING':
        return 4;
      case 'VERIFYING':
        return 5;
      case 'COMPLETED':
        return 6;
      default:
        return 0;
    }
  };

  const PIPELINE_STAGES = [
    'UNDERSTAND',
    'INSPECT CONTEXT',
    'PLAN',
    'AUTHORIZATION',
    'EXECUTE',
    'VERIFY',
    'COMPLETE'
  ];

  // -------------------------------------------------------------
  // RENDER 1: Dedicated Active Task Workspace
  // -------------------------------------------------------------
  if (activeTask) {
    const currentStage = getStageIndex(activeTask.status);

    return (
      <div className="ws-screen" style={{ maxWidth: '1000px', margin: '0 auto' }}>
        {/* Back navigation */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <button
            className="btn btn-secondary"
            onClick={() => navigate('/tasks')}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}
          >
            <ArrowLeft size={14} />
            <span>Back to Tasks</span>
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: '#93c5fd' }}>
              {activeTask.task_id}
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>•</span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              {activeTask.task_type || 'GENERAL'}
            </span>
            {renderStatusBadge(activeTask.status)}
          </div>
        </div>

        {/* Task Objective Title Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            padding: '20px',
            marginBottom: '20px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '8px',
                background: 'rgba(99, 102, 241, 0.1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--accent-brand)',
                flexShrink: 0
              }}
            >
              <Sparkles size={20} />
            </div>
            <div style={{ flex: 1 }}>
              <h2 style={{ fontSize: '18px', fontWeight: 600, color: '#fff', margin: '0 0 6px 0', lineHeight: '1.4' }}>
                {activeTask.objective}
              </h2>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', fontSize: '12px', color: 'var(--text-muted)' }}>
                <span>User: <strong style={{ color: '#fff' }}>{activeTask.user_id}</strong></span>
                {activeTask.dry_run && <span style={{ color: '#fbbf24', fontWeight: 500 }}>○ Dry Run Mode (Simulated)</span>}
                {activeTask.created_at && <span>Created: {new Date(activeTask.created_at).toLocaleTimeString()}</span>}
              </div>
            </div>
          </div>

          {/* Lifecycle Pipeline Progress Bar */}
          <div style={{ marginTop: '20px', paddingTop: '16px', borderTop: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', position: 'relative' }}>
              {PIPELINE_STAGES.map((stage, idx) => {
                const isPassed = currentStage > idx || activeTask.status === 'COMPLETED';
                const isCurrent = currentStage === idx && activeTask.status !== 'COMPLETED';
                return (
                  <div key={stage} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 }}>
                    <div
                      style={{
                        width: '20px',
                        height: '20px',
                        borderRadius: '50%',
                        background: isPassed ? '#10b981' : isCurrent ? 'var(--accent-brand)' : 'rgba(255,255,255,0.1)',
                        color: '#fff',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '11px',
                        marginBottom: '6px',
                        fontWeight: 600,
                        boxShadow: isCurrent ? '0 0 10px rgba(99, 102, 241, 0.5)' : 'none'
                      }}
                    >
                      {isPassed ? <CheckCircle2 size={12} /> : idx + 1}
                    </div>
                    <span
                      style={{
                        fontSize: '10px',
                        fontWeight: isCurrent ? 600 : 500,
                        color: isPassed ? '#34d399' : isCurrent ? '#fff' : 'var(--text-muted)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.04em',
                        textAlign: 'center'
                      }}
                    >
                      {stage}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {error && (
          <div style={{ padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '8px', color: '#fca5a5', fontSize: '13px', marginBottom: '20px' }}>
            {error}
          </div>
        )}

        {/* Clarification Box (WAITING_FOR_USER) */}
        {activeTask.status === 'WAITING_FOR_USER' && (
          <div
            style={{
              padding: '20px',
              marginBottom: '20px',
              background: 'rgba(245, 158, 11, 0.08)',
              border: '1px solid rgba(245, 158, 11, 0.4)',
              borderRadius: '10px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fbbf24', fontWeight: 600, marginBottom: '10px' }}>
              <HelpCircle size={20} />
              <span style={{ fontSize: '15px' }}>Agent Needs Clarification</span>
            </div>

            {activeTask.clarification_questions?.map((q, idx) => (
              <p key={idx} style={{ margin: '0 0 14px 0', fontSize: '14px', whiteSpace: 'pre-wrap', lineHeight: '1.6', color: '#fef3c7' }}>
                {q}
              </p>
            ))}

            {/* Quick Option Pills */}
            {activeTask.clarification_options && activeTask.clarification_options.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                <span style={{ fontSize: '12px', color: '#d97706', fontWeight: 600, textTransform: 'uppercase' }}>
                  Suggested Options:
                </span>
                {activeTask.clarification_options.map((opt, oIdx) => (
                  <button
                    key={oIdx}
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => handleSendClarificationReply(opt.value || opt.label)}
                    disabled={replying}
                    style={{
                      textAlign: 'left',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      background: 'rgba(0,0,0,0.25)',
                      padding: '10px 14px'
                    }}
                  >
                    <span style={{ fontSize: '13px', color: '#fff' }}>{opt.label}</span>
                    <ArrowRight size={14} color="#fbbf24" />
                  </button>
                ))}
              </div>
            )}

            {/* Manual Reply Input */}
            <div style={{ display: 'flex', gap: '10px' }}>
              <input
                type="text"
                className="ws-input"
                placeholder="Type your response to continue this task..."
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSendClarificationReply();
                }}
                disabled={replying}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => handleSendClarificationReply()}
                disabled={replying || !replyText.trim()}
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                {replying ? <InlineSpinner /> : <Send size={14} />}
                <span>Reply</span>
              </button>
            </div>
          </div>
        )}

        {/* Execution Plan View */}
        {activeTask.execution_plan && (
          <div
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '10px',
              padding: '20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h3 style={{ fontSize: '14px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: 0 }}>
                Execution Plan ({activeTask.execution_plan.steps?.length || 0} steps)
              </h3>
              {activeTask.execution_plan.required_tools?.length > 0 && (
                <div style={{ display: 'flex', gap: '6px' }}>
                  {activeTask.execution_plan.required_tools.map((t, idx) => (
                    <span key={idx} style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', padding: '2px 8px', borderRadius: '4px', background: 'rgba(255,255,255,0.05)', color: '#93c5fd' }}>
                      tool:{t}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {activeTask.execution_plan.steps?.map((step, idx) => {
                const isStepCompleted = step.completed || activeTask.status === 'COMPLETED';
                const isStepRunning = activeTask.status === 'RUNNING' && !step.completed;

                return (
                  <div
                    key={idx}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '12px',
                      padding: '10px 14px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '6px',
                      fontSize: '13px'
                    }}
                  >
                    {isStepCompleted ? (
                      <CheckCircle2 size={16} color="#10b981" />
                    ) : isStepRunning ? (
                      <InlineSpinner />
                    ) : (
                      <Clock size={16} color="var(--text-muted)" />
                    )}
                    <strong style={{ color: 'var(--text-secondary)' }}>Step {step.step_number}:</strong>
                    <span style={{ color: '#fff', flex: 1 }}>{step.description}</span>
                    {step.requires_authorization && (
                      <span style={{ fontSize: '11px', color: '#fbbf24', padding: '2px 6px', background: 'rgba(245,158,11,0.1)', borderRadius: '4px' }}>
                        Requires Authorization
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Action Cards & Provider Verification */}
        {activeTask.actions && activeTask.actions.length > 0 && (
          <div
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '10px',
              padding: '20px',
              marginBottom: '20px'
            }}
          >
            <h3 style={{ fontSize: '14px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 14px 0' }}>
              Concrete Actions & Provider Verification ({activeTask.actions.length})
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {activeTask.actions.map((act, aIdx) => (
                <div
                  key={aIdx}
                  style={{
                    padding: '16px',
                    background: 'rgba(0,0,0,0.25)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '8px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {act.action_type === 'SEND_EMAIL' ? <Mail size={16} color="#60a5fa" /> : <Search size={16} color="#a78bfa" />}
                      <strong style={{ fontSize: '14px', color: '#fff' }}>{act.description || act.action_type}</strong>
                    </div>

                    <span
                      style={{
                        fontSize: '11px',
                        fontWeight: 600,
                        padding: '3px 8px',
                        borderRadius: '4px',
                        background:
                          act.status === 'SUCCEEDED'
                            ? 'rgba(16,185,129,0.15)'
                            : act.status === 'FAILED'
                            ? 'rgba(239,68,68,0.15)'
                            : 'rgba(255,255,255,0.06)',
                        color:
                          act.status === 'SUCCEEDED'
                            ? '#34d399'
                            : act.status === 'FAILED'
                            ? '#f87171'
                            : '#aaa'
                      }}
                    >
                      {act.result?.status === 'DRY_RUN' ? 'SIMULATED (DRY RUN)' : act.status}
                    </span>
                  </div>

                  {/* Email Details */}
                  {act.parameters?.to_email && (
                    <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                      <span>To: <strong style={{ color: '#fff' }}>{act.parameters.to_email}</strong></span>
                      {act.parameters?.subject && (
                        <span style={{ marginLeft: '14px' }}>
                          Subject: <strong style={{ color: '#fff' }}>{act.parameters.subject}</strong>
                        </span>
                      )}
                    </div>
                  )}

                  {/* Body Content Preview */}
                  {act.parameters?.body && (
                    <div
                      style={{
                        marginTop: '8px',
                        padding: '12px',
                        background: 'rgba(255,255,255,0.03)',
                        border: '1px solid rgba(255,255,255,0.05)',
                        borderRadius: '6px',
                        fontSize: '13px',
                        lineHeight: '1.6',
                        color: '#e4e4e7',
                        whiteSpace: 'pre-wrap'
                      }}
                    >
                      {act.parameters.body}
                    </div>
                  )}

                  {/* Real Provider Verification Receipt */}
                  {act.result?.provider_message_id || act.result?.message_id ? (
                    <div
                      style={{
                        marginTop: '10px',
                        fontSize: '12px',
                        color: '#34d399',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        background: 'rgba(16,185,129,0.08)',
                        padding: '8px 12px',
                        borderRadius: '6px'
                      }}
                    >
                      <ShieldCheck size={16} />
                      <span>
                        Verified by {act.result?.provider || 'Gmail'} — Message ID:{' '}
                        <code style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                          {act.result.provider_message_id || act.result.message_id}
                        </code>
                      </span>
                    </div>
                  ) : act.result?.status === 'DRY_RUN' ? (
                    <div
                      style={{
                        marginTop: '10px',
                        fontSize: '12px',
                        color: '#fbbf24',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        background: 'rgba(245,158,11,0.08)',
                        padding: '8px 12px',
                        borderRadius: '6px'
                      }}
                    >
                      <ShieldCheck size={16} />
                      <span>Simulated dry-run verified. Zero external provider calls executed.</span>
                    </div>
                  ) : act.error ? (
                    <div
                      style={{
                        marginTop: '10px',
                        fontSize: '12px',
                        color: '#f87171',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        background: 'rgba(239,68,68,0.08)',
                        padding: '8px 12px',
                        borderRadius: '6px'
                      }}
                    >
                      <AlertCircle size={16} />
                      <span>Execution error: {act.error}</span>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Scoped Authorization Action Bar (WAITING_FOR_AUTHORIZATION) */}
        {activeTask.status === 'WAITING_FOR_AUTHORIZATION' && (
          <div
            style={{
              padding: '18px 20px',
              background: 'rgba(59, 130, 246, 0.08)',
              border: '1px solid rgba(59, 130, 246, 0.4)',
              borderRadius: '10px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
              <div style={{ width: '40px', height: '40px', borderRadius: '8px', background: 'rgba(59, 130, 246, 0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#60a5fa' }}>
                <ShieldCheck size={24} />
              </div>
              <div>
                <strong style={{ fontSize: '15px', display: 'block', color: '#fff', marginBottom: '2px' }}>
                  Explicit Authorization Required
                </strong>
                <span style={{ fontSize: '13px', color: '#93c5fd' }}>
                  This task is ready to dispatch {activeTask.actions?.length || 1} email action(s) through your connected account.
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '10px' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleCancel}
                disabled={cancelling || approving}
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                <XCircle size={14} />
                <span>Cancel</span>
              </button>

              <button
                type="button"
                className="btn btn-primary"
                onClick={handleApprove}
                disabled={approving || cancelling}
                style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#3b82f6' }}
              >
                {approving ? <InlineSpinner /> : <Play size={14} />}
                <span>Authorize & Run</span>
              </button>
            </div>
          </div>
        )}

        {/* Live Activity Milestones */}
        {activeTask.events && activeTask.events.length > 0 && (
          <div
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '10px',
              padding: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
              <Activity size={16} color="var(--accent-brand)" />
              <h3 style={{ fontSize: '14px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: 0 }}>
                Task Activity & Audit Milestones
              </h3>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {activeTask.events.map((evt, eIdx) => (
                <div
                  key={eIdx}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    background: 'rgba(255,255,255,0.02)',
                    borderRadius: '6px',
                    fontSize: '12px'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', color: '#93c5fd', fontSize: '11px' }}>
                      [{evt.event_type}]
                    </span>
                    <span style={{ color: '#fff' }}>{evt.message}</span>
                  </div>
                  <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                    {evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  // -------------------------------------------------------------
  // RENDER 2: Clean New Task / Objective Composer
  // -------------------------------------------------------------
  return (
    <div className="ws-screen" style={{ maxWidth: '850px', margin: '40px auto 0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '12px',
            background: 'rgba(99, 102, 241, 0.12)',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-brand)',
            marginBottom: '16px'
          }}
        >
          <Sparkles size={26} />
        </div>
        <h1 style={{ fontSize: '28px', fontWeight: 700, color: '#fff', margin: '0 0 8px 0', letterSpacing: '-0.02em' }}>
          What can I help you accomplish?
        </h1>
        <p style={{ fontSize: '15px', color: 'var(--text-secondary)', maxWidth: '600px', margin: '0 auto', lineHeight: '1.5' }}>
          Describe any objective in plain English. Resolve AI will reason across context, inspect files, formulate a verifiable plan, and execute verified actions.
        </p>
      </div>

      {/* Main Composer Box */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '12px',
          padding: '20px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
          marginBottom: '24px'
        }}
      >
        <form onSubmit={handleRunTask}>
          <textarea
            className="ws-input"
            style={{
              width: '100%',
              minHeight: '120px',
              resize: 'vertical',
              fontSize: '15px',
              lineHeight: '1.6',
              background: 'transparent',
              border: 'none',
              padding: '4px',
              outline: 'none',
              boxShadow: 'none'
            }}
            placeholder="Tell Resolve AI what you want to accomplish...&#10;e.g. 'Send an email to Ujjwal Sharma at sharmaujjwal2019@gmail.com requesting pre-seed funding for my startup CKRIPT.'"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            disabled={loading}
          />

          {/* Attachments chips */}
          {attachments.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', margin: '14px 0', paddingTop: '12px', borderTop: '1px solid var(--border-subtle)' }}>
              {attachments.map((att, idx) => (
                <span
                  key={idx}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '4px 10px',
                    background: 'rgba(255,255,255,0.06)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '6px',
                    fontSize: '12px',
                    color: '#fff'
                  }}
                >
                  <FileText size={13} color="var(--accent-brand)" />
                  <strong>{att.filename}</strong>
                  <button
                    type="button"
                    onClick={() => removeAttachment(idx)}
                    style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: 0, marginLeft: '4px' }}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Action Row */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              paddingTop: '16px',
              borderTop: '1px solid var(--border-subtle)',
              marginTop: '12px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                onChange={handleFileUpload}
                multiple
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingFile || loading}
                style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}
              >
                <Paperclip size={14} />
                <span>{uploadingFile ? 'Uploading...' : '+ Attach Files (PDF, DOCX, CSV, XLSX)'}</span>
              </button>

              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(e) => setDryRun(e.target.checked)}
                  disabled={loading}
                />
                <span>Dry run (simulate only)</span>
              </label>
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !objective.trim()}
              style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 20px', fontSize: '14px', fontWeight: 600 }}
            >
              {loading ? <InlineSpinner /> : <Sparkles size={16} />}
              <span>{loading ? 'Planning...' : 'Run Objective'}</span>
            </button>
          </div>
        </form>
      </div>

      {error && (
        <div style={{ padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '8px', color: '#fca5a5', fontSize: '13px', marginBottom: '20px' }}>
          {error}
        </div>
      )}

      {/* Suggested Prompt Cards */}
      <div style={{ marginTop: '28px' }}>
        <h3 style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '12px' }}>
          Suggested Objectives
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          {[
            {
              title: 'Send Pre-Seed Outreach',
              desc: 'Send an email to Ujjwal Sharma at sharmaujjwal2019@gmail.com requesting pre-seed funding for my startup CKRIPT.',
              icon: Mail
            },
            {
              title: 'Analyze Investor Spreadsheet',
              desc: 'Read the attached investor spreadsheet and identify the investors who are relevant for CKRIPT. Then prepare personalized outreach emails for them.',
              icon: FileText
            },
            {
              title: 'Cross-Document Synthesis',
              desc: 'Read these three documents and summarize the important information.',
              icon: Search
            },
            {
              title: 'Find Angels & Competitors',
              desc: 'Research competitors for CKRIPT and find prospective angels interested in autonomous execution agents.',
              icon: Sparkles
            }
          ].map((item, idx) => {
            const Icon = item.icon;
            return (
              <div
                key={idx}
                onClick={() => setObjective(item.desc)}
                style={{
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '8px',
                  padding: '14px',
                  cursor: 'pointer',
                  transition: 'border-color 0.15s ease'
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--border-active)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                  <Icon size={15} color="var(--accent-brand)" />
                  <strong style={{ fontSize: '13px', color: '#fff' }}>{item.title}</strong>
                </div>
                <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0, lineHeight: '1.4' }}>
                  {item.desc}
                </p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

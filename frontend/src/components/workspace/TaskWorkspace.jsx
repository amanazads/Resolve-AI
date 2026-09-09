import React, { useState, useEffect, useRef } from 'react';
import {
  ArrowLeft,
  Sparkles,
  Paperclip,
  CheckCircle2,
  Clock,
  AlertTriangle,
  Play,
  RotateCcw,
  X,
  FileText,
  Mail,
  ShieldCheck,
  Send,
  ExternalLink,
  ChevronRight,
  Terminal,
  Activity,
  Layers,
  HelpCircle,
  Pause,
  StopCircle,
  Eye,
  RefreshCw
} from 'lucide-react';
import {
  getTask,
  createTask,
  sendTaskMessage,
  approveTask,
  resumeTask,
  pauseTask,
  cancelTask,
  uploadTaskAttachment,
  listTasks,
  connectGmail
} from '../../services/api';
import { navigate } from '../../router';
import { InlineSpinner } from '../ui/States';

export default function TaskWorkspace({ initialTaskId = null }) {
  // Mode: Composer if no activeTask, else Workspace
  const [activeTask, setActiveTask] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Composer states
  const [objective, setObjective] = useState('');
  const [dryRun, setDryRun] = useState(false);
  const [attachments, setAttachments] = useState([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [recentTasks, setRecentTasks] = useState([]);
  const [loadingRecent, setLoadingRecent] = useState(false);
  const fileInputRef = useRef(null);

  // Active Task Workspace states
  const [clarificationInput, setClarificationInput] = useState('');
  const [actionInProgress, setActionInProgress] = useState(false);
  const [showDevDetails, setShowDevDetails] = useState(false);
  const [activeTab, setActiveTab] = useState('overview'); // overview, payload, timeline, dev

  // Load recent tasks for Composer
  useEffect(() => {
    if (!initialTaskId) {
      setActiveTask(null);
      loadRecentTasks();
    } else {
      loadTask(initialTaskId);
    }
  }, [initialTaskId]);

  const loadRecentTasks = async () => {
    setLoadingRecent(true);
    try {
      const res = await listTasks({ limit: 6 });
      setRecentTasks(res?.items || []);
    } catch (e) {
      console.error('Failed to load recent tasks', e);
    } finally {
      setLoadingRecent(false);
    }
  };

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

  // Real-time polling while in-flight
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
        console.error('Task poll error', e);
      }
    }, 1200);

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

  const handleStartTask = async (e) => {
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
      setError(err?.message || 'Failed to initialize task execution');
    } finally {
      setLoading(false);
    }
  };

  const handleAuthorize = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      const updated = await approveTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Authorization failed');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleClarificationResponse = async (answer) => {
    if (!activeTask || !answer?.trim()) return;
    setActionInProgress(true);
    try {
      const updated = await sendTaskMessage(activeTask.task_id, answer.trim());
      setActiveTask(updated);
      setClarificationInput('');
    } catch (err) {
      setError(err?.message || 'Failed to submit clarification');
    } finally {
      setActionInProgress(false);
    }
  };

  const handlePause = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      const updated = await pauseTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Failed to pause task');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleResume = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      const updated = await resumeTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Failed to resume task');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleCancel = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      const updated = await cancelTask(activeTask.task_id);
      setActiveTask(updated);
    } catch (err) {
      setError(err?.message || 'Failed to cancel task');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleReconnectGmail = async () => {
    try {
      const res = await connectGmail({
        redirectAfter: `${window.location.origin}${window.location.pathname}#/connections`
      });
      if (res?.authorization_url) {
        window.location.href = res.authorization_url;
      }
    } catch (err) {
      setError(err?.message || 'Could not initiate Google OAuth');
    }
  };

  // Helper formatting functions
  const renderStatusBadge = (status) => {
    const s = (status || '').toUpperCase();
    if (s === 'COMPLETED') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '11px', fontWeight: 600 }}>
          <CheckCircle2 size={12} /> Completed
        </span>
      );
    }
    if (s === 'WAITING_FOR_AUTHORIZATION') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', border: '1px solid rgba(245, 158, 11, 0.3)', fontSize: '11px', fontWeight: 600 }}>
          <ShieldCheck size={12} /> Needs Authorization
        </span>
      );
    }
    if (s === 'WAITING_FOR_USER' || s === 'WAITING_FOR_CLARIFICATION') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(147, 197, 253, 0.15)', color: '#93c5fd', border: '1px solid rgba(147, 197, 253, 0.3)', fontSize: '11px', fontWeight: 600 }}>
          <HelpCircle size={12} /> Needs Clarification
        </span>
      );
    }
    if (s === 'RUNNING' || s === 'VERIFYING' || s === 'PLANNING' || s === 'UNDERSTANDING' || s === 'INSPECTING') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(99, 102, 241, 0.15)', color: '#818cf8', border: '1px solid rgba(99, 102, 241, 0.3)', fontSize: '11px', fontWeight: 600 }}>
          <InlineSpinner /> {s.replace(/_/g, ' ')}
        </span>
      );
    }
    if (s === 'FAILED') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(239, 68, 68, 0.15)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.3)', fontSize: '11px', fontWeight: 600 }}>
          <AlertTriangle size={12} /> Failed
        </span>
      );
    }
    if (s === 'PAUSED') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(255, 255, 255, 0.08)', color: '#cbd5e1', border: '1px solid rgba(255, 255, 255, 0.15)', fontSize: '11px', fontWeight: 600 }}>
          <Pause size={12} /> Paused
        </span>
      );
    }
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(255, 255, 255, 0.05)', color: '#94a3b8', fontSize: '11px', fontWeight: 600 }}>
        {s}
      </span>
    );
  };

  // Find primary email action and generated payload
  const primaryEmailAction = (activeTask?.actions || []).find((a) => a.action_type === 'SEND_EMAIL');
  const emailParams = primaryEmailAction?.parameters || {};
  const emailResult = primaryEmailAction?.result || (activeTask?.results || [])[0]?.result;
  const isGmailSuccess = emailResult?.success && emailResult?.provider === 'gmail';
  const isDryRunResult = activeTask?.dry_run || emailResult?.status === 'DRY_RUN' || emailResult?.provider === 'simulated';

  // =========================================================================
  // SCREEN 1: ACTIVE TASK WORKSPACE (/tasks/:id)
  // =========================================================================
  if (activeTask) {
    return (
      <div style={{ maxWidth: '960px', margin: '0 auto' }}>
        {/* Navigation & Status Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <button
            className="btn btn-secondary"
            onClick={() => navigate('/tasks')}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', padding: '6px 12px' }}
          >
            <ArrowLeft size={14} />
            <span>Tasks</span>
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-muted)' }}>
              {activeTask.task_id}
            </span>
            {renderStatusBadge(activeTask.status)}
            {activeTask.dry_run && (
              <span style={{ fontSize: '11px', color: '#fbbf24', background: 'rgba(245, 158, 11, 0.1)', border: '1px solid rgba(245, 158, 11, 0.25)', padding: '2px 8px', borderRadius: '10px', fontWeight: 500 }}>
                Dry Run (Simulated)
              </span>
            )}
          </div>
        </div>

        {/* Task Objective Banner */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            padding: '20px',
            marginBottom: '20px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '8px',
                  background: 'rgba(99, 102, 241, 0.15)',
                  color: 'var(--accent-brand)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0
                }}
              >
                <Sparkles size={20} />
              </div>
              <div>
                <h1 style={{ fontSize: '18px', fontWeight: 600, color: '#fff', margin: '0 0 6px 0', lineHeight: 1.4 }}>
                  {activeTask.objective}
                </h1>
                <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: 'var(--text-muted)' }}>
                  <span>User: <strong style={{ color: '#fff' }}>{activeTask.user_id}</strong></span>
                  <span>Created: {new Date(activeTask.created_at).toLocaleTimeString()}</span>
                  <span>Actions: <strong>{(activeTask.actions || []).length}</strong></span>
                </div>
              </div>
            </div>

            {/* Task Controls: Pause / Resume / Cancel */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {activeTask.status === 'RUNNING' && (
                <button className="btn btn-secondary" onClick={handlePause} disabled={actionInProgress} style={{ fontSize: '12px', padding: '5px 10px' }}>
                  <Pause size={13} /> Pause
                </button>
              )}
              {activeTask.status === 'PAUSED' && (
                <button className="btn btn-primary" onClick={handleResume} disabled={actionInProgress} style={{ fontSize: '12px', padding: '5px 10px' }}>
                  <Play size={13} /> Resume
                </button>
              )}
              {!['COMPLETED', 'FAILED', 'CANCELLED'].includes(activeTask.status) && (
                <button className="btn btn-secondary" onClick={handleCancel} disabled={actionInProgress} style={{ fontSize: '12px', padding: '5px 10px', color: '#f87171' }}>
                  <X size={13} /> Cancel
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Section 22: AGENT RESPONSE / CONVERSATION PANEL */}
        <div
          style={{
            background: 'linear-gradient(180deg, rgba(99, 102, 241, 0.05) 0%, rgba(24, 24, 27, 0.8) 100%)',
            border: '1px solid rgba(99, 102, 241, 0.2)',
            borderRadius: '10px',
            padding: '16px 20px',
            marginBottom: '20px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <div style={{ width: '18px', height: '18px', borderRadius: '4px', background: 'var(--accent-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: '10px', fontWeight: 700 }}>
              R
            </div>
            <strong style={{ fontSize: '13px', color: '#fff' }}>Resolve AI</strong>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Autonomous Reasoning</span>
          </div>
          <p style={{ fontSize: '14px', lineHeight: 1.6, color: '#e4e4e7', margin: 0 }}>
            {activeTask.status === 'WAITING_FOR_USER' || activeTask.status === 'WAITING_FOR_CLARIFICATION'
              ? (activeTask.clarification_questions?.[0] || 'I need additional clarification to complete your objective.')
              : activeTask.status === 'WAITING_FOR_AUTHORIZATION'
              ? `I have formulated a plan and prepared the email for ${emailParams.to_email || 'the recipient'}. Before executing external actions, I require explicit authorization to send 1 email from your connected Gmail account.`
              : activeTask.status === 'COMPLETED'
              ? (activeTask.dry_run
                  ? `Task execution simulated successfully. Generated copy and verified parameters. No external messages were sent.`
                  : `Task completed successfully. Email was accepted by the external provider and confirmed.`)
              : activeTask.status === 'FAILED'
              ? `Execution stopped: ${activeTask.errors?.[0] || 'An error occurred during tool execution.'}`
              : `Executing objective. Inspecting context, evaluating requirements, and preparing deterministic actions.`}
          </p>
        </div>

        {/* Section 23: CLARIFICATION INTERACTION BOX */}
        {(activeTask.status === 'WAITING_FOR_USER' || activeTask.status === 'WAITING_FOR_CLARIFICATION') && (
          <div
            style={{
              background: 'rgba(59, 130, 246, 0.08)',
              border: '1px solid rgba(59, 130, 246, 0.3)',
              borderRadius: '10px',
              padding: '18px 20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <HelpCircle size={16} color="#60a5fa" />
              <strong style={{ fontSize: '14px', color: '#93c5fd' }}>Clarification Required</strong>
            </div>

            {/* Quick Option Pills */}
            {activeTask.clarification_options?.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Select one of the matching contacts:</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {activeTask.clarification_options.map((opt, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleClarificationResponse(opt.value || opt.label)}
                      disabled={actionInProgress}
                      style={{
                        padding: '8px 14px',
                        background: 'rgba(255, 255, 255, 0.06)',
                        border: '1px solid rgba(59, 130, 246, 0.3)',
                        borderRadius: '6px',
                        color: '#fff',
                        fontSize: '13px',
                        cursor: 'pointer',
                        textAlign: 'left'
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Freeform Reply Input */}
            <div style={{ display: 'flex', gap: '10px' }}>
              <input
                type="text"
                className="ws-input"
                placeholder="Type your clarification response..."
                value={clarificationInput}
                onChange={(e) => setClarificationInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && clarificationInput.trim()) {
                    handleClarificationResponse(clarificationInput);
                  }
                }}
                disabled={actionInProgress}
                style={{ flex: 1, padding: '8px 12px', fontSize: '13px' }}
              />
              <button
                className="btn btn-primary"
                onClick={() => handleClarificationResponse(clarificationInput)}
                disabled={actionInProgress || !clarificationInput.trim()}
                style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 16px', fontSize: '13px' }}
              >
                {actionInProgress ? <InlineSpinner /> : <Send size={14} />}
                <span>Continue Task</span>
              </button>
            </div>
          </div>
        )}

        {/* Section 6: CLEAR AUTHORIZATION REQUIRED PROMPT */}
        {activeTask.status === 'WAITING_FOR_AUTHORIZATION' && (
          <div
            style={{
              background: 'rgba(245, 158, 11, 0.08)',
              border: '1px solid rgba(245, 158, 11, 0.4)',
              borderRadius: '10px',
              padding: '20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <ShieldCheck size={18} color="#fbbf24" />
              <strong style={{ fontSize: '15px', color: '#fbbf24' }}>Authorization Required</strong>
            </div>
            <p style={{ fontSize: '13px', color: '#e4e4e7', marginBottom: '14px', lineHeight: 1.5 }}>
              Resolve AI wants to send <strong>1 email</strong> from your connected <strong>Gmail</strong> account.
            </p>

            <div style={{ background: 'rgba(0,0,0,0.3)', padding: '12px 16px', borderRadius: '6px', marginBottom: '16px', fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div><span style={{ color: 'var(--text-muted)' }}>To:</span> <strong style={{ color: '#fff' }}>{primaryEmailAction?.description || 'Recipient'}</strong></div>
              <div><span style={{ color: 'var(--text-muted)' }}>Email:</span> <strong style={{ color: '#fff' }}>{emailParams.to_email || 'Not specified'}</strong></div>
              <div><span style={{ color: 'var(--text-muted)' }}>Subject:</span> <strong style={{ color: '#fff' }}>{emailParams.subject || 'Pre-Seed Outreach'}</strong></div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
              <button className="btn btn-secondary" onClick={handleCancel} disabled={actionInProgress} style={{ padding: '8px 16px', fontSize: '13px' }}>
                Cancel Task
              </button>
              <button
                className="btn btn-primary"
                onClick={handleAuthorize}
                disabled={actionInProgress}
                style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 20px', fontSize: '13px', background: '#10b981', borderColor: '#10b981', color: '#fff', fontWeight: 600 }}
              >
                {actionInProgress ? <InlineSpinner /> : <ShieldCheck size={15} />}
                <span>Authorize & Send</span>
              </button>
            </div>
          </div>
        )}

        {/* Section 14: FAILED EXECUTION BOX */}
        {activeTask.status === 'FAILED' && (
          <div
            style={{
              background: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.4)',
              borderRadius: '10px',
              padding: '20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <AlertTriangle size={18} color="#f87171" />
              <strong style={{ fontSize: '15px', color: '#f87171' }}>Task Execution Failed</strong>
            </div>
            <p style={{ fontSize: '13px', color: '#e4e4e7', marginBottom: '10px' }}>
              Resolve AI could not send the email.
            </p>
            <div style={{ background: 'rgba(0,0,0,0.3)', padding: '10px 14px', borderRadius: '6px', marginBottom: '16px', fontSize: '12px', fontFamily: 'var(--font-mono)', color: '#fca5a5' }}>
              {activeTask.errors?.[0] || 'Provider request rejected.'}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                The email was <strong>NOT</strong> sent. No external message left your account.
              </span>
              <button
                className="btn btn-secondary"
                onClick={handleReconnectGmail}
                style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#93c5fd' }}
              >
                <RefreshCw size={13} /> Reconnect Gmail
              </button>
            </div>
          </div>
        )}

        {/* Section 15: DRY RUN COMPLETED NOTIFICATION */}
        {activeTask.status === 'COMPLETED' && isDryRunResult && (
          <div
            style={{
              background: 'rgba(245, 158, 11, 0.08)',
              border: '1px solid rgba(245, 158, 11, 0.3)',
              borderRadius: '10px',
              padding: '16px 20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <Sparkles size={16} color="#fbbf24" />
              <strong style={{ fontSize: '14px', color: '#fbbf24' }}>DRY RUN — No External Actions Performed</strong>
            </div>
            <div style={{ fontSize: '13px', color: '#e4e4e7', lineHeight: 1.5 }}>
              <div>✓ Recipient identified: <strong>{emailParams.to_email}</strong></div>
              <div>✓ Outreach copy formulated and validated</div>
              <div>○ Email NOT sent because Dry Run was enabled</div>
            </div>
          </div>
        )}

        {/* Sections 9, 10, 11, 12, 13: REAL PROVIDER EXECUTION RECEIPT */}
        {activeTask.status === 'COMPLETED' && !isDryRunResult && isGmailSuccess && (
          <div
            style={{
              background: 'rgba(16, 185, 129, 0.08)',
              border: '1px solid rgba(16, 185, 129, 0.4)',
              borderRadius: '10px',
              padding: '20px',
              marginBottom: '20px'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle2 size={20} color="#10b981" />
                <div>
                  <strong style={{ fontSize: '15px', color: '#10b981' }}>✓ Gmail accepted the message</strong>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>The message was successfully submitted to Gmail API.</div>
                </div>
              </div>

              {/* Section 12: Provider link to Sent folder */}
              <a
                href="https://mail.google.com/mail/u/0/#sent"
                target="_blank"
                rel="noreferrer"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 12px',
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '6px',
                  color: '#fff',
                  fontSize: '12px',
                  textDecoration: 'none'
                }}
              >
                <span>Open in Gmail</span>
                <ExternalLink size={12} />
              </a>
            </div>

            {/* Provider Receipts */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', background: 'rgba(0,0,0,0.3)', padding: '14px', borderRadius: '8px', fontSize: '12px' }}>
              <div>
                <span style={{ color: 'var(--text-muted)' }}>Provider:</span>
                <div style={{ color: '#fff', fontWeight: 600, textTransform: 'capitalize' }}>{emailResult.provider || 'Gmail'}</div>
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)' }}>Message ID:</span>
                <div style={{ color: '#93c5fd', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{emailResult.message_id || 'Captured'}</div>
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)' }}>Thread ID:</span>
                <div style={{ color: '#93c5fd', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{emailResult.thread_id || 'Captured'}</div>
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)' }}>Sent At:</span>
                <div style={{ color: '#fff' }}>{emailResult.sent_at ? new Date(emailResult.sent_at).toLocaleString() : 'Just now'}</div>
              </div>
            </div>
          </div>
        )}

        {/* Workspace Sub-Tabs: Overview, Email Draft, Timeline, Dev Details */}
        <div style={{ display: 'flex', gap: '8px', borderBottom: '1px solid var(--border-subtle)', marginBottom: '20px' }}>
          {[
            { key: 'overview', label: 'Plan & Actions', icon: Layers },
            { key: 'payload', label: 'Email Content', icon: Mail },
            { key: 'timeline', label: 'Activity Log', icon: Activity },
            { key: 'dev', label: 'Developer Details', icon: Terminal }
          ].map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '10px 16px',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: active ? '2px solid var(--accent-brand)' : '2px solid transparent',
                  color: active ? '#fff' : 'var(--text-muted)',
                  fontSize: '13px',
                  fontWeight: active ? 600 : 400,
                  cursor: 'pointer'
                }}
              >
                <Icon size={14} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* TAB 1: OVERVIEW / PLAN & ACTIONS */}
        {activeTab === 'overview' && (
          <div>
            {/* Step-by-Step Plan */}
            {activeTask.execution_plan?.steps?.length > 0 && (
              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px', marginBottom: '20px' }}>
                <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 14px 0' }}>
                  Execution Plan
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {activeTask.execution_plan.steps.map((step, idx) => (
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
                      <div
                        style={{
                          width: '22px',
                          height: '22px',
                          borderRadius: '50%',
                          background: step.completed || activeTask.status === 'COMPLETED' ? '#10b981' : 'rgba(255,255,255,0.1)',
                          color: '#fff',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: '11px',
                          fontWeight: 600
                        }}
                      >
                        {step.completed || activeTask.status === 'COMPLETED' ? <CheckCircle2 size={13} /> : idx + 1}
                      </div>
                      <span style={{ color: '#e4e4e7' }}>{step.description}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Concrete Action Cards */}
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px', marginBottom: '20px' }}>
              <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 14px 0' }}>
                Actions ({(activeTask.actions || []).length})
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {(activeTask.actions || []).map((action, idx) => (
                  <div
                    key={idx}
                    style={{
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      padding: '14px 16px',
                      background: 'rgba(255,255,255,0.01)'
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '11px', fontWeight: 600, padding: '2px 8px', background: 'rgba(99, 102, 241, 0.1)', color: 'var(--accent-brand)', borderRadius: '4px' }}>
                          {action.action_type}
                        </span>
                        <strong style={{ fontSize: '13px', color: '#fff' }}>{action.description}</strong>
                      </div>
                      {renderStatusBadge(action.status)}
                    </div>
                    {action.parameters?.to_email && (
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        Recipient: <span style={{ color: '#fff' }}>{action.parameters.to_email}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Attached Context Files */}
            {activeTask.attachments?.length > 0 && (
              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px' }}>
                <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 14px 0' }}>
                  Attached Documents ({activeTask.attachments.length})
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {activeTask.attachments.map((att, idx) => (
                    <div
                      key={idx}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 14px',
                        background: 'rgba(255,255,255,0.02)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '6px',
                        fontSize: '13px'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <FileText size={16} color="var(--accent-brand)" />
                        <span style={{ color: '#fff', fontWeight: 500 }}>{att.filename}</span>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>({(att.size_bytes / 1024).toFixed(1)} KB)</span>
                      </div>
                      <span style={{ fontSize: '11px', color: '#10b981', background: 'rgba(16, 185, 129, 0.1)', padding: '2px 8px', borderRadius: '4px' }}>
                        Extracted & Isolated
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB 2: SECTION 5 - MAKE THE EMAIL VISIBLE BEFORE SENDING */}
        {activeTab === 'payload' && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '24px' }}>
            <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 16px 0' }}>
              Generated Email Payload
            </h3>

            {emailParams.body || emailParams.subject ? (
              <div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', paddingBottom: '16px', borderBottom: '1px solid var(--border-subtle)', marginBottom: '16px', fontSize: '13px' }}>
                  <div><span style={{ color: 'var(--text-muted)' }}>To:</span> <strong style={{ color: '#fff' }}>{primaryEmailAction?.description || 'Recipient'} &lt;{emailParams.to_email}&gt;</strong></div>
                  <div><span style={{ color: 'var(--text-muted)' }}>Subject:</span> <strong style={{ color: '#fff' }}>{emailParams.subject}</strong></div>
                </div>

                <div
                  style={{
                    background: 'rgba(0,0,0,0.2)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '8px',
                    padding: '16px 20px',
                    fontSize: '14px',
                    lineHeight: 1.7,
                    color: '#e4e4e7',
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'var(--font-sans)'
                  }}
                >
                  {emailParams.body}
                </div>
              </div>
            ) : (
              <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                No email payload generated yet. Email content will be prepared during the planning phase.
              </div>
            )}
          </div>
        )}

        {/* TAB 3: SECTION 4 & 19 - REAL-TIME AGENT ACTIVITY TIMELINE */}
        {activeTab === 'timeline' && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '24px' }}>
            <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 16px 0' }}>
              Execution Event Timeline
            </h3>

            {activeTask.events?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                {activeTask.events.map((evt, idx) => (
                  <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', fontSize: '13px' }}>
                    <div style={{ marginTop: '2px', color: '#10b981' }}>
                      <CheckCircle2 size={15} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ color: '#fff', fontWeight: 500 }}>{evt.message}</div>
                      <div style={{ display: 'flex', gap: '10px', fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                        <span>{new Date(evt.timestamp).toLocaleTimeString()}</span>
                        <span>•</span>
                        <span style={{ fontFamily: 'var(--font-mono)' }}>{evt.event_type}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                No activity events recorded yet.
              </div>
            )}
          </div>
        )}

        {/* TAB 4: SECTION 29 - DEVELOPER DETAILS */}
        {activeTab === 'dev' && (
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px' }}>
            <h3 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 14px 0' }}>
              Developer Diagnostics
            </h3>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px', fontSize: '12px' }}>
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Task ID:</span>
                <div style={{ fontFamily: 'var(--font-mono)', color: '#93c5fd' }}>{activeTask.task_id}</div>
              </div>
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>User ID:</span>
                <div style={{ fontFamily: 'var(--font-mono)', color: '#93c5fd' }}>{activeTask.user_id}</div>
              </div>
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Action ID:</span>
                <div style={{ fontFamily: 'var(--font-mono)', color: '#93c5fd' }}>{primaryEmailAction?.action_id || 'act_primary'}</div>
              </div>
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Idempotency Key:</span>
                <div style={{ fontFamily: 'var(--font-mono)', color: '#93c5fd' }}>{primaryEmailAction?.idempotency_key || `${activeTask.task_id}_${primaryEmailAction?.action_id || 'act'}`}</div>
              </div>
            </div>

            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Raw Task Object:</span>
            <pre
              style={{
                marginTop: '6px',
                padding: '14px',
                background: 'rgba(0,0,0,0.4)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '6px',
                fontSize: '11px',
                fontFamily: 'var(--font-mono)',
                color: '#cbd5e1',
                maxHeight: '320px',
                overflow: 'auto'
              }}
            >
              {JSON.stringify(activeTask, null, 2)}
            </pre>
          </div>
        )}
      </div>
    );
  }

  // =========================================================================
  // SCREEN 2: SECTION 2 - AGENT WORKSPACE NEW TASK COMPOSER
  // =========================================================================
  return (
    <div style={{ maxWidth: '840px', margin: '0 auto', paddingTop: '12px' }}>
      {/* Workspace Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, color: '#fff', margin: '0 0 6px 0', letterSpacing: '-0.02em' }}>
          New Task
        </h1>
        <p style={{ fontSize: '14px', color: 'var(--text-secondary)', margin: 0 }}>
          What do you want me to accomplish?
        </p>
      </div>

      {/* Main Objective Input Box */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '12px',
          padding: '20px',
          boxShadow: '0 8px 30px rgba(0,0,0,0.4)',
          marginBottom: '36px'
        }}
      >
        <form onSubmit={handleStartTask}>
          <textarea
            className="ws-input"
            style={{
              width: '100%',
              minHeight: '120px',
              resize: 'vertical',
              fontSize: '15px',
              lineHeight: 1.6,
              background: 'transparent',
              border: 'none',
              padding: '4px',
              outline: 'none',
              boxShadow: 'none',
              color: '#fff'
            }}
            placeholder="Describe your objective... (e.g. 'Send an email to Ujjwal Sharma at sharmaujjwal2019@gmail.com asking if he would be interested in discussing pre-seed funding for CKRIPT.')"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            disabled={loading}
          />

          {/* Attached Files Chips */}
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

          {/* Controls Toolbar */}
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
                style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', padding: '6px 12px' }}
              >
                <Paperclip size={14} />
                <span>{uploadingFile ? 'Uploading...' : '+ Attach files'}</span>
              </button>

              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(e) => setDryRun(e.target.checked)}
                  disabled={loading}
                />
                <span>Dry run</span>
              </label>
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !objective.trim()}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 22px',
                fontSize: '14px',
                fontWeight: 600,
                background: 'linear-gradient(135deg, #6366f1, #4f46e5)',
                boxShadow: '0 2px 10px rgba(99, 102, 241, 0.3)'
              }}
            >
              {loading ? <InlineSpinner /> : <Play size={14} fill="#fff" />}
              <span>{loading ? 'Starting...' : 'Start Task'}</span>
            </button>
          </div>
        </form>
      </div>

      {error && (
        <div style={{ padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '8px', color: '#fca5a5', fontSize: '13px', marginBottom: '24px' }}>
          {error}
        </div>
      )}

      {/* SECTION 2: RECENT TASKS */}
      <div style={{ marginTop: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: 600, color: '#fff', margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Recent Tasks
          </h2>
          <button
            onClick={() => navigate('/tasks')}
            style={{ background: 'none', border: 'none', color: 'var(--accent-brand)', fontSize: '13px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}
          >
            <span>View all</span>
            <ChevronRight size={14} />
          </button>
        </div>

        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', overflow: 'hidden' }}>
          {loadingRecent ? (
            <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              <InlineSpinner /> Loading recent tasks...
            </div>
          ) : recentTasks.length === 0 ? (
            <div style={{ padding: '36px 20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              No tasks executed yet. Describe your first objective above to start!
            </div>
          ) : (
            <div>
              {recentTasks.map((t, idx) => (
                <div
                  key={t.task_id || idx}
                  onClick={() => navigate(`/tasks/${t.task_id}`)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '14px 20px',
                    borderBottom: idx === recentTasks.length - 1 ? 'none' : '1px solid var(--border-subtle)',
                    cursor: 'pointer',
                    transition: 'background 0.15s ease'
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0, marginRight: '16px' }}>
                    <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: t.status === 'COMPLETED' ? '#10b981' : t.status === 'FAILED' ? '#ef4444' : 'var(--accent-brand)', flexShrink: 0 }}></div>
                    <strong style={{ fontSize: '13px', color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.objective}
                    </strong>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexShrink: 0 }}>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {new Date(t.created_at).toLocaleDateString()}
                    </span>
                    {renderStatusBadge(t.status)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

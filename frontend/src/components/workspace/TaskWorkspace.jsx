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
  ArrowRight
} from 'lucide-react';
import {
  createTask,
  listTasks,
  getTask,
  sendTaskMessage,
  approveTask,
  uploadTaskAttachment
} from '../../services/api';
import { Card, PageHeader, StatusPill } from '../ui/Primitives';
import { LoadingState, InlineSpinner, ErrorState } from '../ui/States';

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
  const [error, setError] = useState(null);

  // Recent tasks list
  const [recentTasks, setRecentTasks] = useState([]);
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);

  const fetchRecentTasks = async () => {
    try {
      const res = await listTasks({ limit: 15 });
      setRecentTasks(res?.tasks || []);
    } catch (e) {
      console.error('Failed to load tasks', e);
    }
  };

  useEffect(() => {
    fetchRecentTasks();
    if (initialTaskId) {
      loadTask(initialTaskId);
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

  // Poll active task if in progress
  useEffect(() => {
    if (!activeTask?.task_id) return;
    const terminalStatuses = ['COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_FOR_USER', 'WAITING_FOR_AUTHORIZATION'];
    if (terminalStatuses.includes(activeTask.status)) return;

    const interval = setInterval(async () => {
      try {
        const updated = await getTask(activeTask.task_id);
        setActiveTask(updated);
        if (terminalStatuses.includes(updated.status)) {
          clearInterval(interval);
          fetchRecentTasks();
        }
      } catch (e) {
        console.error('Poll error', e);
      }
    }, 2000);

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
      fetchRecentTasks();
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
      fetchRecentTasks();
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
      fetchRecentTasks();
    } catch (err) {
      setError(err?.message || 'Failed to approve task execution');
    } finally {
      setApproving(false);
    }
  };

  const renderStatusBadge = (status) => {
    switch (status) {
      case 'UNDERSTANDING':
      case 'PLANNING':
        return <StatusPill tone="info" label={status} />;
      case 'WAITING_FOR_USER':
        return <StatusPill tone="warning" label="Waiting for Clarification" />;
      case 'WAITING_FOR_AUTHORIZATION':
        return <StatusPill tone="warning" label="Awaiting Approval" />;
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

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Autonomous Agent"
        title="Task Workspace"
        description="Describe any objective in plain English, attach files or resumes, and let Resolve AI inspect, clarify, plan, and execute verified actions."
      />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: '20px', alignItems: 'start' }}>
        {/* Main interactive area */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Creator card */}
          <Card title="New Objective" subtitle="Resolve AI will reason across your objective, files, and tools.">
            <form onSubmit={handleRunTask}>
              <div style={{ marginBottom: '14px' }}>
                <textarea
                  className="ws-input"
                  style={{ width: '100%', minHeight: '90px', resize: 'vertical', fontSize: '14px', lineHeight: '1.5' }}
                  placeholder="What do you want Resolve AI to do?&#10;Examples:&#10;• 'Send an email to Aman saying hello'&#10;• 'Send emails to Aman and Ujjwal'&#10;• 'Use my resume and recruiter CSV to contact relevant founders'&#10;• 'What is your refund policy?'"
                  value={objective}
                  onChange={(e) => setObjective(e.target.value)}
                  disabled={loading}
                />
              </div>

              {/* Attachments chips */}
              {attachments.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '14px' }}>
                  {attachments.map((att, idx) => (
                    <span
                      key={idx}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '4px 10px',
                        background: 'rgba(255,255,255,0.06)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '6px',
                        fontSize: '12px'
                      }}
                    >
                      <FileText size={13} />
                      <strong>{att.filename}</strong>
                      <button
                        type="button"
                        onClick={() => removeAttachment(idx)}
                        style={{ background: 'none', border: 'none', color: '#aaa', cursor: 'pointer', padding: 0 }}
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Action controls */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
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
                    <span>{uploadingFile ? 'Uploading...' : 'Attach Files (CSV, PDF, DOCX)'}</span>
                  </button>

                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={dryRun}
                      onChange={(e) => setDryRun(e.target.checked)}
                      disabled={loading}
                    />
                    <span>Dry Run (simulate only)</span>
                  </label>
                </div>

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={loading || !objective.trim()}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  {loading ? <InlineSpinner /> : <Sparkles size={14} />}
                  <span>{loading ? 'Reasoning...' : 'Run Objective'}</span>
                </button>
              </div>
            </form>
          </Card>

          {error && (
            <div style={{ padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid #ef4444', borderRadius: '8px', color: '#fca5a5', fontSize: '13px' }}>
              {error}
            </div>
          )}

          {/* Active Task Execution Timeline */}
          {activeTask && (
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                  <span style={{ fontSize: '15px', fontWeight: 600 }}>Task: {activeTask.objective}</span>
                  {renderStatusBadge(activeTask.status)}
                </div>
              }
            >
              {/* Clarification Box (WAITING_FOR_USER) */}
              {activeTask.status === 'WAITING_FOR_USER' && (
                <div
                  style={{
                    padding: '16px',
                    marginBottom: '16px',
                    background: 'rgba(245, 158, 11, 0.08)',
                    border: '1px solid rgba(245, 158, 11, 0.4)',
                    borderRadius: '8px'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fbbf24', fontWeight: 600, marginBottom: '8px' }}>
                    <HelpCircle size={18} />
                    <span>Agent Clarification Needed</span>
                  </div>

                  {activeTask.clarification_questions?.map((q, idx) => (
                    <p key={idx} style={{ margin: '0 0 12px 0', fontSize: '14px', whiteSpace: 'pre-wrap', lineHeight: '1.6' }}>
                      {q}
                    </p>
                  ))}

                  {/* Clarification quick option buttons */}
                  {activeTask.clarification_options && activeTask.clarification_options.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '14px' }}>
                      {activeTask.clarification_options.map((opt, oIdx) => (
                        <button
                          key={oIdx}
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => handleSendClarificationReply(opt.value || opt.label)}
                          disabled={replying}
                          style={{ textAlign: 'left', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                        >
                          <span>{opt.label}</span>
                          <ArrowRight size={14} />
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Manual reply input */}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <input
                      type="text"
                      className="ws-input"
                      placeholder="Type your response or clarification..."
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
                    >
                      {replying ? <InlineSpinner /> : <Send size={14} />}
                    </button>
                  </div>
                </div>
              )}

              {/* Execution Plan View */}
              {activeTask.execution_plan && (
                <div style={{ marginBottom: '20px' }}>
                  <h4 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#888', marginBottom: '10px' }}>
                    Execution Plan ({activeTask.execution_plan.steps?.length || 0} steps)
                  </h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {activeTask.execution_plan.steps?.map((step, idx) => (
                      <div
                        key={idx}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '10px',
                          padding: '8px 12px',
                          background: 'rgba(255,255,255,0.03)',
                          border: '1px solid rgba(255,255,255,0.06)',
                          borderRadius: '6px',
                          fontSize: '13px'
                        }}
                      >
                        {step.completed || activeTask.status === 'COMPLETED' ? (
                          <CheckCircle2 size={16} color="#10b981" />
                        ) : activeTask.status === 'RUNNING' ? (
                          <InlineSpinner />
                        ) : (
                          <Clock size={16} color="#6b7280" />
                        )}
                        <span style={{ fontWeight: 500 }}>Step {step.step_number}:</span>
                        <span style={{ color: '#ccc' }}>{step.description}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Action Preview & Execution Cards */}
              {activeTask.actions && activeTask.actions.length > 0 && (
                <div style={{ marginBottom: '20px' }}>
                  <h4 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#888', marginBottom: '10px' }}>
                    Actions & Results ({activeTask.actions.length})
                  </h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {activeTask.actions.map((act, aIdx) => (
                      <div
                        key={aIdx}
                        style={{
                          padding: '12px',
                          background: 'rgba(255,255,255,0.02)',
                          border: '1px solid rgba(255,255,255,0.08)',
                          borderRadius: '8px'
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            {act.action_type === 'SEND_EMAIL' ? <Mail size={15} color="#60a5fa" /> : <Search size={15} color="#a78bfa" />}
                            <strong style={{ fontSize: '13px' }}>{act.description || act.action_type}</strong>
                          </div>
                          <span
                            style={{
                              fontSize: '11px',
                              fontWeight: 600,
                              padding: '2px 8px',
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
                                  : '#bbb'
                            }}
                          >
                            {act.result?.status === 'DRY_RUN' ? 'SIMULATED (DRY RUN)' : act.status}
                          </span>
                        </div>

                        {/* Email preview details */}
                        {act.parameters?.to_email && (
                          <div style={{ fontSize: '12px', color: '#aaa', marginBottom: '4px' }}>
                            To: <span style={{ color: '#fff' }}>{act.parameters.to_email}</span> | Subject: <span style={{ color: '#fff' }}>{act.parameters.subject}</span>
                          </div>
                        )}

                        {act.parameters?.body && (
                          <div
                            style={{
                              marginTop: '6px',
                              padding: '8px',
                              background: 'rgba(0,0,0,0.25)',
                              borderRadius: '4px',
                              fontSize: '12px',
                              color: '#ddd',
                              whiteSpace: 'pre-wrap',
                              fontFamily: 'monospace'
                            }}
                          >
                            {act.parameters.body}
                          </div>
                        )}

                        {/* RAG response preview */}
                        {act.result?.context_text && (
                          <div style={{ marginTop: '8px', padding: '8px', background: 'rgba(0,0,0,0.3)', borderRadius: '4px', fontSize: '12px' }}>
                            <strong>Grounded Context:</strong>
                            <p style={{ margin: '4px 0 0 0', color: '#ccc' }}>{act.result.context_text}</p>
                          </div>
                        )}

                        {/* Provider Confirmation */}
                        {act.result?.message_id && (
                          <div style={{ marginTop: '6px', fontSize: '11px', color: '#34d399', display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <ShieldCheck size={13} />
                            <span>Confirmed by provider (ID: {act.result.message_id})</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Authorization Action Bar */}
              {activeTask.status === 'WAITING_FOR_AUTHORIZATION' && (
                <div
                  style={{
                    padding: '14px',
                    background: 'rgba(59, 130, 246, 0.08)',
                    border: '1px solid rgba(59, 130, 246, 0.3)',
                    borderRadius: '8px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <ShieldCheck size={20} color="#60a5fa" />
                    <div>
                      <strong style={{ fontSize: '13px', display: 'block' }}>Authorization Required</strong>
                      <span style={{ fontSize: '12px', color: '#93c5fd' }}>
                        Review the prepared actions above. Ready to execute through verified channels.
                      </span>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handleApprove}
                    disabled={approving}
                    style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
                  >
                    {approving ? <InlineSpinner /> : <Play size={14} />}
                    <span>{approving ? 'Executing...' : 'Authorize & Send'}</span>
                  </button>
                </div>
              )}
            </Card>
          )}
        </div>

        {/* Recent Tasks Rail */}
        <div>
          <Card title="Task History" subtitle="Your recent autonomous jobs">
            {recentTasks.length === 0 ? (
              <p style={{ fontSize: '13px', color: '#666' }}>No previous tasks found.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {recentTasks.map((t) => (
                  <button
                    key={t.task_id}
                    onClick={() => loadTask(t.task_id)}
                    style={{
                      textAlign: 'left',
                      padding: '10px',
                      background: activeTask?.task_id === t.task_id ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.02)',
                      border: '1px solid ' + (activeTask?.task_id === t.task_id ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.05)'),
                      borderRadius: '6px',
                      cursor: 'pointer',
                      color: '#fff',
                      transition: 'background 0.15s ease'
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                      <span style={{ fontSize: '11px', color: '#888' }}>{t.task_type || 'TASK'}</span>
                      {renderStatusBadge(t.status)}
                    </div>
                    <div style={{ fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.objective}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

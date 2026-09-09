import React, { useState, useEffect } from 'react';
import {
  Sparkles,
  Plus,
  RefreshCw,
  Clock,
  CheckCircle2,
  AlertCircle,
  Play,
  HelpCircle,
  ShieldCheck,
  Search,
  Filter
} from 'lucide-react';
import { listTasks } from '../../services/api';
import { navigate } from '../../router';
import { Card, PageHeader, StatusPill } from '../ui/Primitives';
import { InlineSpinner, LoadingState } from '../ui/States';

export default function TasksList() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filterStatus, setFilterStatus] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  const fetchTasks = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listTasks({ limit: 100 });
      setTasks(res?.tasks || []);
    } catch (err) {
      setError(err?.message || 'Failed to load task history');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, []);

  // Filter tasks
  const filteredTasks = tasks.filter((t) => {
    if (filterStatus === 'RUNNING' && !['UNDERSTANDING', 'PLANNING', 'RUNNING', 'VERIFYING'].includes(t.status)) return false;
    if (filterStatus === 'ACTION_REQUIRED' && !['WAITING_FOR_USER', 'WAITING_FOR_AUTHORIZATION'].includes(t.status)) return false;
    if (filterStatus === 'COMPLETED' && t.status !== 'COMPLETED') return false;
    if (filterStatus === 'FAILED' && t.status !== 'FAILED') return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const obj = (t.objective || '').toLowerCase();
      const id = (t.task_id || '').toLowerCase();
      if (!obj.includes(q) && !id.includes(q)) return false;
    }
    return true;
  });

  // Group tasks by date
  const groupTasksByDate = (taskList) => {
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400000).toDateString();

    const groups = {
      Today: [],
      Yesterday: [],
      Earlier: []
    };

    taskList.forEach((t) => {
      const dateStr = t.created_at ? new Date(t.created_at).toDateString() : today;
      if (dateStr === today) {
        groups.Today.push(t);
      } else if (dateStr === yesterday) {
        groups.Yesterday.push(t);
      } else {
        groups.Earlier.push(t);
      }
    });

    return groups;
  };

  const grouped = groupTasksByDate(filteredTasks);

  const renderStatusBadge = (status) => {
    switch (status) {
      case 'UNDERSTANDING':
      case 'PLANNING':
        return <StatusPill tone="info" label="Planning" />;
      case 'WAITING_FOR_USER':
        return <StatusPill tone="warning" label="Needs Clarification" />;
      case 'WAITING_FOR_AUTHORIZATION':
        return <StatusPill tone="warning" label="Needs Approval" />;
      case 'RUNNING':
      case 'VERIFYING':
        return <StatusPill tone="info" label="Executing" />;
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
    <div className="ws-screen" style={{ maxWidth: '950px', margin: '0 auto' }}>
      <PageHeader
        eyebrow="Persistent Autonomous Agents"
        title="Tasks"
        description="Every natural-language objective given to Resolve AI produces a persistent task that inspects context, plans, executes tools, and verifies completion."
        actions={
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-secondary" onClick={fetchTasks} disabled={loading} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <RefreshCw size={14} className={loading ? 'spinning' : ''} />
              <span>Refresh</span>
            </button>
            <button className="btn btn-primary" onClick={() => navigate('/new')} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Plus size={14} />
              <span>New Task</span>
            </button>
          </div>
        }
      />

      {/* Filter and Search Bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: '6px' }}>
          {[
            { id: 'ALL', label: 'All Tasks' },
            { id: 'RUNNING', label: 'Active' },
            { id: 'ACTION_REQUIRED', label: 'Action Required' },
            { id: 'COMPLETED', label: 'Completed' },
            { id: 'FAILED', label: 'Failed' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setFilterStatus(tab.id)}
              style={{
                padding: '6px 12px',
                borderRadius: '6px',
                fontSize: '12px',
                fontWeight: 500,
                border: '1px solid ' + (filterStatus === tab.id ? 'var(--accent-brand)' : 'var(--border-subtle)'),
                background: filterStatus === tab.id ? 'rgba(99, 102, 241, 0.15)' : 'rgba(255,255,255,0.02)',
                color: filterStatus === tab.id ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer'
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div style={{ position: 'relative', width: '240px' }}>
          <Search size={14} style={{ position: 'absolute', left: '10px', top: '10px', color: 'var(--text-muted)' }} />
          <input
            type="text"
            className="ws-input"
            style={{ width: '100%', paddingLeft: '32px', fontSize: '12px' }}
            placeholder="Search objectives..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {loading && tasks.length === 0 ? (
        <LoadingState label="Loading tasks..." />
      ) : filteredTasks.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px' }}>
          <Sparkles size={32} color="var(--accent-brand)" style={{ marginBottom: '12px', opacity: 0.8 }} />
          <h3 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '6px' }}>No tasks found</h3>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
            {searchQuery ? 'Try clearing your search query' : 'Give Resolve AI your first objective to see persistent task execution.'}
          </p>
          <button className="btn btn-primary" onClick={() => navigate('/new')}>
            Start New Task
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {Object.entries(grouped).map(([groupName, groupList]) => {
            if (groupList.length === 0) return null;
            return (
              <div key={groupName}>
                <h4 style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '10px' }}>
                  {groupName} ({groupList.length})
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {groupList.map((t) => {
                    const actionCount = (t.actions || []).length;
                    const completedActions = (t.actions || []).filter((a) => a.status === 'SUCCEEDED').length;

                    return (
                      <div
                        key={t.task_id}
                        onClick={() => navigate(`/tasks/${t.task_id}`)}
                        style={{
                          background: 'var(--bg-card)',
                          border: '1px solid var(--border-subtle)',
                          borderRadius: '8px',
                          padding: '14px 18px',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          transition: 'border-color 0.15s ease, transform 0.15s ease'
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.borderColor = 'var(--border-active)';
                          e.currentTarget.style.transform = 'translateY(-1px)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.borderColor = 'var(--border-subtle)';
                          e.currentTarget.style.transform = 'translateY(0)';
                        }}
                      >
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', flex: 1, marginRight: '16px' }}>
                          <span style={{ fontSize: '14px', fontWeight: 500, color: '#fff' }}>
                            {t.objective}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#93c5fd' }}>
                              {t.task_id}
                            </span>
                            <span>•</span>
                            <span>
                              {t.status === 'COMPLETED'
                                ? `Completed ${actionCount} ${actionCount === 1 ? 'action' : 'actions'}`
                                : actionCount > 0
                                ? `${completedActions}/${actionCount} actions completed`
                                : `${t.task_type || 'GENERAL'}`}
                            </span>
                            {t.attachments?.length > 0 && (
                              <>
                                <span>•</span>
                                <span>{t.attachments.length} {t.attachments.length === 1 ? 'file' : 'files'}</span>
                              </>
                            )}
                          </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                          {renderStatusBadge(t.status)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

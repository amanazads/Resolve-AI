import React, { useState, useEffect } from 'react';
import { User, Shield, Server, CheckCircle2, RefreshCw, Save } from 'lucide-react';
import { getCurrentUser, setCurrentUser, checkHealth } from '../../services/api';
import { Card, PageHeader } from '../ui/Primitives';

export default function Settings() {
  const [user, setUser] = useState(getCurrentUser());
  const [name, setName] = useState(user.name || '');
  const [email, setEmail] = useState(user.email || '');
  const [userId, setUserId] = useState(user.user_id || '');
  const [saved, setSaved] = useState(false);

  const [health, setHealth] = useState(null);
  const [checkingHealth, setCheckingHealth] = useState(false);

  const fetchHealth = async () => {
    setCheckingHealth(true);
    try {
      const res = await checkHealth();
      setHealth(res);
    } catch (err) {
      setHealth({ status: 'unreachable', error: err?.message });
    } finally {
      setCheckingHealth(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, []);

  const handleSaveUser = (e) => {
    e.preventDefault();
    setCurrentUser({
      user_id: userId.trim() || 'local_user',
      name: name.trim() || 'Aman Azad',
      email: email.trim() || 'aman@ckript.com'
    });
    setUser(getCurrentUser());
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="ws-screen" style={{ maxWidth: '800px', margin: '0 auto' }}>
      <PageHeader
        eyebrow="Preferences & Environment"
        title="Workspace Settings"
        description="Configure your active operator identity, check backend system health, and manage workspace isolation."
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {/* User Identity Profile Card */}
        <Card
          title={
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <User size={16} color="var(--accent-brand)" />
              <span>Operator Identity & Isolation</span>
            </div>
          }
          subtitle="All tasks, authorization grants, and provider operations are scoped to this identity."
        >
          <form onSubmit={handleSaveUser}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '16px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  Display Name
                </label>
                <input
                  type="text"
                  className="ws-input"
                  style={{ width: '100%' }}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Aman Azad"
                />
              </div>

              <div>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  Sender Email Address
                </label>
                <input
                  type="email"
                  className="ws-input"
                  style={{ width: '100%' }}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="e.g. aman@ckript.com"
                />
              </div>
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                User Identifier (Scopes Tasks & Authorization)
              </label>
              <input
                type="text"
                className="ws-input"
                style={{ width: '100%', fontFamily: 'var(--font-mono)' }}
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="e.g. local_user"
              />
              <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                Sent via <code style={{ color: '#93c5fd' }}>X-User-ID</code> to enforce backend data boundary and prevent cross-user leakage.
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              {saved && (
                <span style={{ fontSize: '13px', color: '#34d399', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <CheckCircle2 size={16} /> Saved identity!
                </span>
              )}
              {!saved && <div />}

              <button type="submit" className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Save size={14} />
                <span>Save Identity</span>
              </button>
            </div>
          </form>
        </Card>

        {/* System & API Health Card */}
        <Card
          title={
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Server size={16} color="#10b981" />
              <span>Backend & Integration Health</span>
            </div>
          }
          subtitle="Real-time connectivity to Resolve AI autonomous execution engine."
          actions={
            <button
              className="btn btn-secondary"
              onClick={fetchHealth}
              disabled={checkingHealth}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', padding: '4px 10px' }}
            >
              <RefreshCw size={12} className={checkingHealth ? 'spinning' : ''} />
              <span>Ping</span>
            </button>
          }
        >
          <div style={{ background: 'rgba(0,0,0,0.3)', padding: '14px', borderRadius: '6px', fontSize: '13px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span style={{ color: 'var(--text-muted)' }}>Status:</span>
              <span style={{ color: health?.status === 'ok' || health?.status === 'healthy' ? '#34d399' : '#f87171', fontWeight: 600 }}>
                {health?.status ? health.status.toUpperCase() : 'CHECKING...'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span style={{ color: 'var(--text-muted)' }}>Database:</span>
              <span style={{ color: '#fff' }}>{health?.database || 'MongoDB Atlas / Active'}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-muted)' }}>Email Engine:</span>
              <span style={{ color: '#fff' }}>{health?.email_provider || 'Gmail OAuth / Active'}</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

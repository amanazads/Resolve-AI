import React, { useEffect, useState } from 'react';
import {
  Mail,
  Search,
  FileText,
  RefreshCw,
  ExternalLink,
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Database,
  Cpu,
  Server,
  Key
} from 'lucide-react';
import {
  listIntegrations,
  connectGmail,
  disconnectGmail,
  getDiagnostics
} from '../../services/api';
import { InlineSpinner } from '../ui/States';

export default function Connections() {
  const [integrations, setIntegrations] = useState([]);
  const [diagnostics, setDiagnostics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [connecting, setConnecting] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [intRes, diagRes] = await Promise.all([
        listIntegrations(),
        getDiagnostics().catch(() => null)
      ]);
      setIntegrations(intRes?.integrations || []);
      setDiagnostics(diagRes);
    } catch (err) {
      setError(err?.message || 'Failed to fetch integrations and diagnostics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleConnectGmail = async () => {
    setConnecting(true);
    try {
      const res = await connectGmail({
        redirectAfter: `${window.location.origin}${window.location.pathname}#/connections`
      });
      if (res?.authorization_url) {
        window.location.href = res.authorization_url;
      }
    } catch (err) {
      setError(err?.message || 'Could not initiate Google OAuth consent flow');
      setConnecting(false);
    }
  };

  const handleDisconnectGmail = async (accountId = 'default') => {
    setConnecting(true);
    try {
      await disconnectGmail(accountId);
      await fetchData();
    } catch (err) {
      setError(err?.message || 'Failed to disconnect Gmail');
    } finally {
      setConnecting(false);
    }
  };

  const gmail = integrations.find((i) => i.id === 'gmail') || {};
  const webSearch = integrations.find((i) => i.id === 'web_search') || {};
  const files = integrations.find((i) => i.id === 'file_intelligence') || {};

  const isGmailFullyConnected = Boolean(gmail.connected && gmail.has_send_scope);
  const isGmailMissingScope = Boolean(gmail.status === 'NEEDS_PERMISSION' || (gmail.email_address && !gmail.has_send_scope));

  return (
    <div style={{ maxWidth: '880px', margin: '0 auto', paddingTop: '12px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 600, color: '#fff', margin: '0 0 6px 0', letterSpacing: '-0.02em' }}>
            Connections
          </h1>
          <p style={{ fontSize: '14px', color: 'var(--text-secondary)', margin: 0 }}>
            Manage external tools, OAuth channels, and verification providers accessible to Resolve AI.
          </p>
        </div>

        <button
          className="btn btn-secondary"
          onClick={fetchData}
          disabled={loading}
          style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}
        >
          <RefreshCw size={14} className={loading ? 'spinning' : ''} />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div style={{ marginBottom: '20px', padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '8px', color: '#fca5a5', fontSize: '13px' }}>
          {error}
        </div>
      )}

      {/* SECTION 8: PRIMARY INTEGRATIONS */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '32px' }}>
        {/* Gmail Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            padding: '20px'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ width: '38px', height: '38px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444' }}>
                <Mail size={20} />
              </div>
              <div>
                <strong style={{ fontSize: '15px', color: '#fff' }}>Gmail</strong>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Send and read email through your authenticated Google workspace or personal account.
                </div>
              </div>
            </div>

            {/* Connection Status Pill */}
            {isGmailFullyConnected ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '11px', fontWeight: 600 }}>
                <CheckCircle2 size={12} /> Connected
              </span>
            ) : isGmailMissingScope ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', border: '1px solid rgba(245, 158, 11, 0.3)', fontSize: '11px', fontWeight: 600 }}>
                <AlertTriangle size={12} /> Missing Send Scope
              </span>
            ) : (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(255, 255, 255, 0.06)', color: 'var(--text-muted)', border: '1px solid var(--border-subtle)', fontSize: '11px', fontWeight: 500 }}>
                Not connected
              </span>
            )}
          </div>

          {/* Details & Actions */}
          <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '14px', marginTop: '10px' }}>
            {isGmailFullyConnected ? (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: '13px' }}>
                  <div style={{ color: '#e4e4e7', marginBottom: '4px' }}>
                    Account: <strong style={{ color: '#fff' }}>{gmail.email_address}</strong>
                  </div>
                  <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: '#10b981' }}>
                    <span>✓ Send email</span>
                    <span>✓ Read email</span>
                  </div>
                </div>
                <button
                  className="btn btn-secondary"
                  onClick={() => handleDisconnectGmail(gmail.account_id)}
                  disabled={connecting}
                  style={{ fontSize: '12px', padding: '6px 14px', color: '#f87171' }}
                >
                  Disconnect
                </button>
              </div>
            ) : isGmailMissingScope ? (
              <div>
                <p style={{ fontSize: '13px', color: '#fbbf24', margin: '0 0 12px 0', lineHeight: 1.5 }}>
                  Account <strong>{gmail.email_address}</strong> is connected, but lacks the <code>gmail.send</code> scope. Resolve AI cannot send real emails until you reconnect and grant send permission.
                </p>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <button
                    className="btn btn-primary"
                    onClick={handleConnectGmail}
                    disabled={connecting}
                    style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', padding: '8px 18px', background: '#fbbf24', color: '#000', fontWeight: 600, border: 'none' }}
                  >
                    {connecting ? <InlineSpinner /> : <Key size={14} />}
                    <span>Reconnect & Authorize Gmail</span>
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => handleDisconnectGmail(gmail.account_id)}
                    disabled={connecting}
                    style={{ fontSize: '12px', padding: '6px 12px' }}
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                  Resolve AI cannot send real emails until you connect a Gmail account.
                </span>
                <button
                  className="btn btn-primary"
                  onClick={handleConnectGmail}
                  disabled={connecting}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', padding: '7px 16px', fontWeight: 600 }}
                >
                  {connecting ? <InlineSpinner /> : <Mail size={14} />}
                  <span>Connect Gmail</span>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Web Search Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            padding: '20px'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ width: '38px', height: '38px', borderRadius: '8px', background: 'rgba(59, 130, 246, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#3b82f6' }}>
                <Search size={20} />
              </div>
              <div>
                <strong style={{ fontSize: '15px', color: '#fff' }}>Web Search</strong>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Live search provider for company background, funding history, and market research.
                </div>
              </div>
            </div>

            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '11px', fontWeight: 600 }}>
              <CheckCircle2 size={12} /> Available
            </span>
          </div>
        </div>

        {/* File Intelligence Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '10px',
            padding: '20px'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ width: '38px', height: '38px', borderRadius: '8px', background: 'rgba(168, 85, 247, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#a855f7' }}>
                <FileText size={20} />
              </div>
              <div>
                <strong style={{ fontSize: '15px', color: '#fff' }}>File Intelligence</strong>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Parser supporting PDF, DOCX, CSV, XLSX, TXT, JSON with untrusted prompt injection isolation.
                </div>
              </div>
            </div>

            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '3px 10px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '11px', fontWeight: 600 }}>
              <CheckCircle2 size={12} /> Available
            </span>
          </div>
        </div>
      </div>

      {/* SECTION 28: ENVIRONMENT DIAGNOSTICS */}
      {diagnostics && (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px' }}>
          <h2 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 16px 0' }}>
            System Health & Environment Diagnostics
          </h2>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '14px' }}>
            {/* Database */}
            <div style={{ background: 'rgba(0,0,0,0.25)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Database</span>
                {diagnostics.database?.healthy ? <CheckCircle2 size={14} color="#10b981" /> : <XCircle size={14} color="#ef4444" />}
              </div>
              <strong style={{ fontSize: '13px', color: '#fff' }}>{diagnostics.database?.name || 'MongoDB'}</strong>
            </div>

            {/* LLM */}
            <div style={{ background: 'rgba(0,0,0,0.25)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>LLM Planner</span>
                {diagnostics.llm?.healthy ? <CheckCircle2 size={14} color="#10b981" /> : <XCircle size={14} color="#ef4444" />}
              </div>
              <strong style={{ fontSize: '13px', color: '#fff' }}>{diagnostics.llm?.model || 'Gemini'}</strong>
            </div>

            {/* Gmail OAuth */}
            <div style={{ background: 'rgba(0,0,0,0.25)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Gmail OAuth</span>
                {diagnostics.gmail_oauth?.healthy ? <CheckCircle2 size={14} color="#10b981" /> : <XCircle size={14} color="#ef4444" />}
              </div>
              <strong style={{ fontSize: '13px', color: '#fff' }}>{diagnostics.gmail_oauth?.configured ? 'Configured' : 'Missing Credentials'}</strong>
            </div>

            {/* Gmail Send Permission */}
            <div style={{ background: 'rgba(0,0,0,0.25)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Gmail Send Scope</span>
                {diagnostics.gmail_account?.has_send_scope ? <CheckCircle2 size={14} color="#10b981" /> : <AlertTriangle size={14} color="#fbbf24" />}
              </div>
              <strong style={{ fontSize: '13px', color: '#fff' }}>{diagnostics.gmail_account?.has_send_scope ? 'Active' : 'Needs Reconnect'}</strong>
            </div>

            {/* Worker */}
            <div style={{ background: 'rgba(0,0,0,0.25)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Worker Engine</span>
                <CheckCircle2 size={14} color="#10b981" />
              </div>
              <strong style={{ fontSize: '13px', color: '#fff' }}>Active & Ready</strong>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

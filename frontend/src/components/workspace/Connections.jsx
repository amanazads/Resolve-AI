import React, { useEffect, useState } from 'react';
import {
  Mail,
  Search,
  FileText,
  Plug,
  RefreshCw,
  ExternalLink,
  ShieldCheck,
  CheckCircle2,
  AlertCircle,
  Clock,
  Trash2
} from 'lucide-react';
import { listIntegrations, connectGmail, disconnectGmail, getGmailStatus } from '../../services/api';
import { Card, PageHeader, StatusPill } from '../ui/Primitives';
import { InlineSpinner } from '../ui/States';

export default function Connections() {
  const [integrations, setIntegrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [connecting, setConnecting] = useState(false);

  const fetchIntegrations = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listIntegrations();
      setIntegrations(res?.integrations || []);
    } catch (err) {
      setError(err?.message || 'Failed to fetch integrations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchIntegrations();
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
      setError(err?.message || 'Could not initiate Google OAuth');
      setConnecting(false);
    }
  };

  const handleDisconnectGmail = async (accountId) => {
    setConnecting(true);
    try {
      await disconnectGmail(accountId);
      await fetchIntegrations();
    } catch (err) {
      setError(err?.message || 'Failed to disconnect Gmail');
    } finally {
      setConnecting(false);
    }
  };

  const gmail = integrations.find((i) => i.id === 'gmail');
  const webSearch = integrations.find((i) => i.id === 'web_search');
  const files = integrations.find((i) => i.id === 'file_intelligence');

  return (
    <div className="ws-screen" style={{ maxWidth: '1000px', margin: '0 auto' }}>
      <PageHeader
        eyebrow="Integrations & Tools"
        title="Connected Services"
        description="Manage the external accounts, search providers, and data channels accessible to Resolve AI during task execution."
        actions={
          <button className="btn btn-secondary" onClick={fetchIntegrations} disabled={loading} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <RefreshCw size={14} className={loading ? 'spinning' : ''} />
            <span>Refresh</span>
          </button>
        }
      />

      {error && (
        <div style={{ marginBottom: '20px', padding: '12px 16px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '8px', color: '#fca5a5', fontSize: '13px' }}>
          {error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '30px' }}>
        {/* Gmail Integration Card */}
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '36px', height: '36px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444' }}>
                  <Mail size={20} />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0 }}>Gmail</h3>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Direct Outreach Dispatch</span>
                </div>
              </div>
              <span
                style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '3px 8px',
                  borderRadius: '4px',
                  background: gmail?.connected ? 'rgba(16,185,129,0.15)' : 'rgba(255,255,255,0.06)',
                  color: gmail?.connected ? '#34d399' : '#888'
                }}
              >
                {gmail?.connected ? 'CONNECTED' : 'DISCONNECTED'}
              </span>
            </div>

            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.5', marginBottom: '16px' }}>
              Send and read email through your authenticated Google workspace or personal account with cryptographic verification.
            </p>

            {gmail?.connected ? (
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '12px', borderRadius: '6px', marginBottom: '16px', fontSize: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Mailbox:</span>
                  <span style={{ color: '#fff', fontWeight: 500 }}>{gmail.email_address || 'Connected Account'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Scopes:</span>
                  <span style={{ color: '#93c5fd' }}>gmail.send, gmail.readonly</span>
                </div>
              </div>
            ) : (
              <div style={{ background: 'rgba(255,255,255,0.02)', padding: '10px', borderRadius: '6px', marginBottom: '16px', fontSize: '12px', color: '#888' }}>
                No active Google OAuth grant. Tasks requiring email will request authorization before sending.
              </div>
            )}
          </div>

          <div>
            {gmail?.connected ? (
              <button
                className="btn btn-secondary"
                onClick={() => handleDisconnectGmail(gmail.account_id)}
                disabled={connecting}
                style={{ width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '6px', color: '#f87171' }}
              >
                <Trash2 size={14} />
                <span>Disconnect Mailbox</span>
              </button>
            ) : (
              <button
                className="btn btn-primary"
                onClick={handleConnectGmail}
                disabled={connecting}
                style={{ width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '6px' }}
              >
                {connecting ? <InlineSpinner /> : <ExternalLink size={14} />}
                <span>Connect Gmail</span>
              </button>
            )}
          </div>
        </div>

        {/* Web Search Integration Card */}
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '36px', height: '36px', borderRadius: '8px', background: 'rgba(59, 130, 246, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#3b82f6' }}>
                  <Search size={20} />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0 }}>Web Search</h3>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Live Information Retrieval</span>
                </div>
              </div>
              <span style={{ fontSize: '11px', fontWeight: 600, padding: '3px 8px', borderRadius: '4px', background: 'rgba(16,185,129,0.15)', color: '#34d399' }}>
                AVAILABLE
              </span>
            </div>

            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.5', marginBottom: '16px' }}>
              Queries live web search engines for company intelligence, prospective investor profiles, and recent news.
            </p>

            <div style={{ background: 'rgba(0,0,0,0.3)', padding: '12px', borderRadius: '6px', marginBottom: '16px', fontSize: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Provider:</span>
                <span style={{ color: '#fff' }}>DuckDuckGo Live Search</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>Status:</span>
                <span style={{ color: '#34d399' }}>Active & Ready</span>
              </div>
            </div>
          </div>

          <div>
            <button className="btn btn-secondary" disabled style={{ width: '100%', opacity: 0.7, cursor: 'default' }}>
              <span>Built-in Tool</span>
            </button>
          </div>
        </div>

        {/* File Intelligence Card */}
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '20px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '36px', height: '36px', borderRadius: '8px', background: 'rgba(168, 85, 247, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#a855f7' }}>
                  <FileText size={20} />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0 }}>File Intelligence</h3>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Multi-Format Parsing</span>
                </div>
              </div>
              <span style={{ fontSize: '11px', fontWeight: 600, padding: '3px 8px', borderRadius: '4px', background: 'rgba(16,185,129,0.15)', color: '#34d399' }}>
                AVAILABLE
              </span>
            </div>

            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.5', marginBottom: '16px' }}>
              Extracts text and structured tables from PDF, DOCX, CSV, XLSX, TXT, and JSON files up to 25MB with security isolation.
            </p>

            <div style={{ background: 'rgba(0,0,0,0.3)', padding: '12px', borderRadius: '6px', marginBottom: '16px', fontSize: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Supported:</span>
                <span style={{ color: '#fff' }}>PDF, DOCX, CSV, XLSX, TXT, JSON</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>Max Size:</span>
                <span style={{ color: '#fff' }}>25 MB per artifact</span>
              </div>
            </div>
          </div>

          <div>
            <button className="btn btn-secondary" disabled style={{ width: '100%', opacity: 0.7, cursor: 'default' }}>
              <span>Built-in Tool</span>
            </button>
          </div>
        </div>
      </div>

      {/* Upcoming Integrations */}
      <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '24px' }}>
        <h4 style={{ fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '14px' }}>
          Coming Soon
        </h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px' }}>
          {[
            { name: 'Linear', desc: 'Sync issues & automate task backlogs' },
            { name: 'Slack', desc: 'Notify channels and team members' },
            { name: 'GitHub', desc: 'Inspect pull requests and code files' },
            { name: 'HubSpot', desc: 'Read and update CRM pipeline contacts' }
          ].map((item, idx) => (
            <div
              key={idx}
              style={{
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '8px',
                padding: '14px',
                opacity: 0.6
              }}
            >
              <strong style={{ display: 'block', fontSize: '14px', marginBottom: '4px' }}>{item.name}</strong>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{item.desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

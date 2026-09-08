import React from 'react';
import { ExternalLink, Linkedin, Mail, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { useAction, useAsync } from '../../hooks/useAsync';
import {
  connectGmail,
  disconnectGmail,
  getGmailStatus,
  listPermissions,
  revokePermission
} from '../../services/api';
import { Card, KeyValue, PageHeader, StatusPill, formatTime } from '../ui/Primitives';
import { EmptyState, ErrorState, InlineSpinner, LoadingState } from '../ui/States';

/**
 * Connected accounts and the permissions granted over them.
 *
 * Nothing on this screen is a credential. The Gmail status endpoint returns the
 * mailbox address, the scopes and the connection state; tokens stay encrypted on
 * the server and are never sent to a browser.
 */
export default function Integrations() {
  const gmail = useAsync(() => getGmailStatus(), []);
  const permissions = useAsync(() => listPermissions(), []);

  const connect = useAction(async () => {
    const response = await connectGmail({
      redirectAfter: `${window.location.origin}${window.location.pathname}#/integrations`
    });
    if (response?.authorization_url) {
      // Google's consent screen. The code is exchanged server-side; the browser
      // never sees a token.
      window.location.href = response.authorization_url;
    }
    return response;
  });

  const disconnect = useAction(async () => {
    await disconnectGmail();
    gmail.reload();
    permissions.reload();
  });

  const revoke = useAction(async (grantId) => {
    await revokePermission(grantId, 'Revoked from the integrations screen.');
    permissions.reload();
  });

  const status = gmail.data || {};
  const connected = Boolean(status.connected);
  const grants = permissions.data?.items || [];

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Integrations"
        title="Connected accounts"
        description="Campaigns act through these accounts. Access tokens are held encrypted on the server and never reach this page."
        actions={
          <button className="ws-btn ws-btn-ghost" onClick={gmail.reload}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      <div className="ws-grid ws-grid-2">
        <Card
          title={
            <span className="ws-integration-title">
              <Mail size={15} /> Gmail
            </span>
          }
          subtitle="Send campaign email through your own mailbox over OAuth 2.0."
          actions={gmail.loading ? <InlineSpinner /> : <StatusPill status={status.state || 'DISCONNECTED'} />}
        >
          {gmail.loading && !gmail.data ? (
            <LoadingState label="Checking Gmail…" rows={2} />
          ) : gmail.error ? (
            <ErrorState error={gmail.error} onRetry={gmail.reload} compact />
          ) : (
            <>
              <KeyValue
                items={[
                  { label: 'Mailbox', value: status.email_address || (connected ? 'unknown' : 'not connected') },
                  { label: 'Account', value: status.account_id },
                  { label: 'Connected', value: connected ? formatTime(status.connected_at) : null },
                  { label: 'Last refreshed', value: connected ? formatTime(status.last_refreshed_at) : null },
                  {
                    label: 'Scopes',
                    value:
                      (status.scopes || []).length > 0 ? (
                        <div className="ws-tag-row">
                          {status.scopes.map((scope) => (
                            <span key={scope} className="ws-tag ws-mono">
                              {scope.replace('https://www.googleapis.com/auth/', '')}
                            </span>
                          ))}
                        </div>
                      ) : null
                  }
                ]}
              />

              {status.needs_reauth ? (
                <div className="ws-note ws-note-warn">
                  <div>
                    <strong>This connection needs to be re-authorized.</strong>
                    <p>{status.detail || 'The stored grant is no longer valid at Google.'}</p>
                  </div>
                </div>
              ) : null}

              {!connected && status.detail ? <p className="ws-muted">{status.detail}</p> : null}

              <div className="ws-row-actions">
                {connected ? (
                  <button
                    className="ws-btn ws-btn-danger"
                    onClick={disconnect.execute}
                    disabled={disconnect.pending}
                  >
                    {disconnect.pending ? <InlineSpinner label="Disconnecting…" /> : (<><Trash2 size={14} /> Disconnect</>)}
                  </button>
                ) : (
                  <button
                    className="ws-btn ws-btn-primary"
                    onClick={connect.execute}
                    disabled={connect.pending}
                  >
                    {connect.pending ? <InlineSpinner label="Opening Google…" /> : (<><ExternalLink size={14} /> Connect Gmail</>)}
                  </button>
                )}
              </div>

              {connect.error ? <ErrorState error={connect.error} compact /> : null}
              {disconnect.error ? <ErrorState error={disconnect.error} compact /> : null}
            </>
          )}
        </Card>

        <Card
          title={
            <span className="ws-integration-title">
              <Linkedin size={15} /> LinkedIn
            </span>
          }
          subtitle="Outreach through LinkedIn."
          actions={<StatusPill status="NOT_CONFIGURED">not available</StatusPill>}
        >
          <p className="ws-muted">
            The permission scope exists (LINKEDIN_ACCESS) but no LinkedIn provider is
            wired up yet, so campaigns cannot select this channel.
          </p>
        </Card>
      </div>

      <Card
        title={
          <span className="ws-integration-title">
            <ShieldCheck size={15} /> Permissions
          </span>
        }
        subtitle="What you have authorized Resolve AI to do on your behalf. Revoking takes effect immediately."
        actions={
          <button className="ws-btn ws-btn-ghost ws-btn-sm" onClick={permissions.reload}>
            <RefreshCw size={13} /> Refresh
          </button>
        }
        padded={false}
      >
        {permissions.loading && !permissions.data ? (
          <LoadingState label="Loading permissions…" rows={2} />
        ) : permissions.error ? (
          <ErrorState error={permissions.error} onRetry={permissions.reload} />
        ) : grants.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No permissions granted"
            description="Approving a campaign records a permission scoped to that campaign, audience and mailbox."
          />
        ) : (
          <div className="ws-table-scroll">
            <table className="ws-table">
              <thead>
                <tr>
                  <th>Scopes</th>
                  <th>Applies to</th>
                  <th>Integration</th>
                  <th>Granted</th>
                  <th>Used</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => (
                  <tr key={grant.grant_id}>
                    <td>
                      <div className="ws-tag-row">
                        {grant.scopes.map((scope) => (
                          <span key={scope} className="ws-tag">
                            {scope}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="ws-cell-muted">
                      {grant.standing ? 'Any campaign' : grant.campaign_id}
                      {grant.audience?.length ? ` · ${grant.audience.join(', ')}` : ''}
                      {grant.max_recipients ? ` · ≤ ${grant.max_recipients}` : ''}
                    </td>
                    <td className="ws-cell-muted">{grant.integration || '—'}</td>
                    <td className="ws-cell-muted">{formatTime(grant.granted_at)}</td>
                    <td className="ws-cell-muted">{grant.usage_count}×</td>
                    <td className="ws-cell-right">
                      <button
                        className="ws-btn ws-btn-ghost ws-btn-sm"
                        onClick={() => revoke.execute(grant.grant_id)}
                        disabled={revoke.pending}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

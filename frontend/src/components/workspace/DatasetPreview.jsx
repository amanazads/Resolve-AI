import React, { useState } from 'react';
import { Search, Sparkles } from 'lucide-react';
import { navigate } from '../../router';
import { useAsync } from '../../hooks/useAsync';
import { getDataset, listContacts } from '../../services/api';
import { Card, PageHeader, StatCard, StatusPill, formatTime } from '../ui/Primitives';
import { EmptyState, ErrorState, LoadingState } from '../ui/States';

const TYPES = ['', 'INVESTOR', 'VC', 'FOUNDER', 'HR', 'RECRUITER', 'OTHER', 'UNKNOWN'];
const PAGE_SIZE = 25;

/**
 * What is actually in a dataset: the import statistics, and the contacts
 * themselves with the classification the backend assigned each one.
 */
export default function DatasetPreview({ datasetId }) {
  const [page, setPage] = useState(1);
  const [type, setType] = useState('');
  const [search, setSearch] = useState('');

  const dataset = useAsync(() => getDataset(datasetId), [datasetId]);
  const contacts = useAsync(
    () =>
      listContacts({
        dataset_id: datasetId,
        page,
        page_size: PAGE_SIZE,
        contact_type: type || undefined,
        search: search.trim() || undefined
      }),
    [datasetId, page, type, search]
  );

  if (dataset.loading && !dataset.data) return <LoadingState label="Loading dataset…" />;
  if (dataset.error && !dataset.data) {
    return (
      <div className="ws-screen">
        <PageHeader title="Dataset" backTo="Contacts" onBack={() => navigate('/datasets')} />
        <ErrorState error={dataset.error} onRetry={dataset.reload} />
      </div>
    );
  }

  const info = dataset.data || {};
  const stats = info.statistics || {};
  const rows = contacts.data?.items || [];
  const totalPages = contacts.data?.total_pages || 1;

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Dataset"
        title={info.filename || datasetId}
        description={`Uploaded ${formatTime(info.uploaded_at)} · ${info.file_type?.toUpperCase() || 'CSV'}`}
        backTo="Contacts"
        onBack={() => navigate('/datasets')}
        actions={
          <button
            className="ws-btn ws-btn-primary"
            onClick={() => navigate('/create', { dataset: datasetId })}
          >
            <Sparkles size={14} /> Create a campaign
          </button>
        }
      />

      <div className="ws-grid ws-grid-5">
        <StatCard label="Valid contacts" value={stats.valid_contacts ?? 0} tone="success" />
        <StatCard label="Invalid" value={stats.invalid_contacts ?? 0} tone={stats.invalid_contacts ? 'warn' : 'neutral'} />
        <StatCard label="Duplicates" value={stats.duplicates ?? 0} tone={stats.duplicates ? 'warn' : 'neutral'} />
        <StatCard label="Investors" value={stats.investors ?? 0} />
        <StatCard label="Recruiters / HR" value={stats.hr ?? 0} />
      </div>

      <Card
        title="Contacts"
        actions={
          <div className="ws-toolbar-right">
            <select
              className="ws-select ws-select-sm"
              value={type}
              onChange={(e) => {
                setType(e.target.value);
                setPage(1);
              }}
            >
              {TYPES.map((option) => (
                <option key={option || 'all'} value={option}>
                  {option || 'All types'}
                </option>
              ))}
            </select>
            <div className="ws-search">
              <Search size={14} />
              <input
                value={search}
                placeholder="Search name, email, company"
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
              />
            </div>
          </div>
        }
        padded={false}
      >
        {contacts.loading && !contacts.data ? (
          <LoadingState label="Loading contacts…" />
        ) : contacts.error ? (
          <ErrorState error={contacts.error} onRetry={contacts.reload} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No contacts match"
            description={search || type ? 'Try clearing the filters.' : 'This dataset has no contacts.'}
          />
        ) : (
          <>
            <div className="ws-table-scroll">
              <table className="ws-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Company</th>
                    <th>Role</th>
                    <th>Type</th>
                    <th>Valid</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((contact) => (
                    <tr key={contact.contact_id}>
                      <td className="ws-cell-main">{contact.full_name || contact.first_name || '—'}</td>
                      <td className="ws-cell-muted">{contact.email || '—'}</td>
                      <td className="ws-cell-muted">{contact.company || '—'}</td>
                      <td className="ws-cell-muted">{contact.role || '—'}</td>
                      <td>
                        <span className="ws-tag">{contact.contact_type}</span>
                      </td>
                      <td>
                        {contact.is_valid ? (
                          <StatusPill status="SENT">valid</StatusPill>
                        ) : (
                          <StatusPill status="FAILED" title={(contact.validation_errors || []).join(', ')}>
                            invalid
                          </StatusPill>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="ws-pager">
              <button
                className="ws-btn ws-btn-ghost ws-btn-sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <span className="ws-muted">
                Page {page} of {totalPages} · {contacts.data?.total ?? 0} contacts
              </span>
              <button
                className="ws-btn ws-btn-ghost ws-btn-sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

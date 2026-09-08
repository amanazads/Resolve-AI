import React, { useRef, useState } from 'react';
import { FileSpreadsheet, Table2, Upload } from 'lucide-react';
import { navigate } from '../../router';
import { useAction, useAsync } from '../../hooks/useAsync';
import { listDatasets, uploadContacts } from '../../services/api';
import { Card, PageHeader, StatCard, formatTime } from '../ui/Primitives';
import { EmptyState, ErrorState, InlineSpinner, LoadingState } from '../ui/States';

const ACCEPTED = '.csv,.xlsx,.xls,.tsv';

/**
 * Contact import. Drop a CSV or XLSX; the backend maps the columns, validates
 * the addresses, de-duplicates and classifies each contact, and reports what it
 * did rather than silently discarding rows.
 */
export default function DatasetUpload() {
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState(null);
  const inputRef = useRef(null);

  const datasets = useAsync(() => listDatasets(1, 50), []);

  const upload = useAction(async (file) => {
    setProgress(0);
    setResult(null);
    const stats = await uploadContacts(file, setProgress);
    setResult(stats);
    datasets.reload();
    return stats;
  });

  const handleFiles = (files) => {
    const file = files?.[0];
    if (file) upload.execute(file);
  };

  const list = datasets.data?.datasets || [];

  return (
    <div className="ws-screen">
      <PageHeader
        eyebrow="Contacts"
        title="Contacts"
        description="Upload the people you want to reach. Everything a campaign sends is drawn from these datasets."
        actions={
          <button className="ws-btn ws-btn-primary" onClick={() => inputRef.current?.click()}>
            <Upload size={14} /> Upload file
          </button>
        }
      />

      <div
        className={`ws-dropzone ${dragging ? 'dragging' : ''} ${upload.pending ? 'busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => !upload.pending && inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED}
          hidden
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = '';
          }}
        />
        {upload.pending ? (
          <>
            <InlineSpinner label={`Uploading and processing… ${progress}%`} />
            <div className="ws-progress">
              <div className="ws-progress-fill ws-tone-brand" style={{ width: `${progress}%` }} />
            </div>
          </>
        ) : (
          <>
            <div className="ws-dropzone-icon">
              <FileSpreadsheet size={22} />
            </div>
            <strong>Drop a CSV or XLSX here</strong>
            <span>Columns are mapped automatically. Invalid and duplicate rows are reported, not silently dropped.</span>
          </>
        )}
      </div>

      {upload.error ? <ErrorState error={upload.error} compact /> : null}

      {result ? (
        <Card title={`Imported ${result.filename || 'file'}`}>
          <div className="ws-grid ws-grid-5">
            <StatCard label="Rows read" value={result.total_rows} />
            <StatCard label="Valid" value={result.valid_contacts} tone="success" />
            <StatCard
              label="Invalid"
              value={result.invalid_contacts}
              tone={result.invalid_contacts ? 'warn' : 'neutral'}
            />
            <StatCard
              label="Duplicates"
              value={result.duplicates}
              tone={result.duplicates ? 'warn' : 'neutral'}
            />
            <StatCard label="Investors" value={result.investors} />
          </div>
          {result.dataset_id ? (
            <div className="ws-row-actions">
              <button
                className="ws-btn ws-btn-ghost"
                onClick={() => navigate(`/datasets/${result.dataset_id}`)}
              >
                <Table2 size={14} /> Preview contacts
              </button>
              <button
                className="ws-btn ws-btn-primary"
                onClick={() => navigate('/create', { dataset: result.dataset_id })}
              >
                Create a campaign from this
              </button>
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card title="Datasets" subtitle="Everything uploaded so far">
        {datasets.loading && !datasets.data ? (
          <LoadingState label="Loading datasets…" rows={2} />
        ) : datasets.error ? (
          <ErrorState error={datasets.error} onRetry={datasets.reload} />
        ) : list.length === 0 ? (
          <EmptyState
            icon={Table2}
            title="No contacts uploaded yet"
            description="Upload a CSV or XLSX to get started."
          />
        ) : (
          <div className="ws-table-scroll">
            <table className="ws-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Valid</th>
                  <th>Invalid</th>
                  <th>Duplicates</th>
                  <th>Uploaded</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((dataset) => (
                  <tr key={dataset.dataset_id}>
                    <td>
                      <div className="ws-cell-main">{dataset.filename}</div>
                      <div className="ws-cell-sub ws-mono">{dataset.dataset_id}</div>
                    </td>
                    <td>{dataset.statistics?.valid_contacts ?? 0}</td>
                    <td className="ws-cell-muted">{dataset.statistics?.invalid_contacts ?? 0}</td>
                    <td className="ws-cell-muted">{dataset.statistics?.duplicates ?? 0}</td>
                    <td className="ws-cell-muted">{formatTime(dataset.uploaded_at)}</td>
                    <td className="ws-cell-right">
                      <button
                        className="ws-btn ws-btn-ghost ws-btn-sm"
                        onClick={() => navigate(`/datasets/${dataset.dataset_id}`)}
                      >
                        Preview
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

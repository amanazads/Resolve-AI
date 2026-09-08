import React, { useState } from 'react';
import { CornerDownLeft, Sparkles } from 'lucide-react';
import { navigate } from '../../router';

export const OBJECTIVE_KEY = 'resolve.draft.objective';
export const DATASET_KEY = 'resolve.draft.dataset';

const EXAMPLES = [
  'Send personalized fundraising emails to investors in this sheet.',
  'Reach out to the recruiters in my list about senior backend roles.',
  'Ask the founders in this dataset for 15 minutes of feedback.'
];

/**
 * The natural-language entry point.
 *
 * Deliberately does not plan in place: it hands the objective to the campaign
 * builder, where the plan can be reviewed properly before anything is approved.
 */
export default function ObjectiveComposer({ datasets = [], datasetsLoading = false }) {
  const [objective, setObjective] = useState('');
  const [datasetId, setDatasetId] = useState('');

  const submit = (text) => {
    const value = (text ?? objective).trim();
    if (!value) return;
    try {
      sessionStorage.setItem(OBJECTIVE_KEY, value);
      sessionStorage.setItem(DATASET_KEY, datasetId || '');
    } catch {
      /* private mode: the objective is passed on the route instead */
    }
    navigate('/create', datasetId ? { dataset: datasetId } : undefined);
  };

  return (
    <div className="ws-composer">
      <div className="ws-composer-head">
        <Sparkles size={15} />
        <span>Describe an objective</span>
      </div>

      <textarea
        className="ws-composer-input"
        rows={3}
        value={objective}
        placeholder="e.g. Send personalized fundraising emails to investors in this sheet."
        onChange={(e) => setObjective(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
        }}
      />

      <div className="ws-composer-foot">
        <label className="ws-select-inline">
          <span>Contacts</span>
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
            disabled={datasetsLoading || datasets.length === 0}
          >
            <option value="">
              {datasetsLoading
                ? 'Loading…'
                : datasets.length === 0
                  ? 'No datasets uploaded'
                  : 'Let the agent choose'}
            </option>
            {datasets.map((dataset) => (
              <option key={dataset.dataset_id} value={dataset.dataset_id}>
                {dataset.filename} ({dataset.statistics?.valid_contacts ?? 0})
              </option>
            ))}
          </select>
        </label>

        <button className="ws-btn ws-btn-primary" onClick={() => submit()} disabled={!objective.trim()}>
          Plan it <CornerDownLeft size={13} />
        </button>
      </div>

      <div className="ws-example-chips">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            className="ws-chip"
            onClick={() => {
              setObjective(example);
              submit(example);
            }}
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}

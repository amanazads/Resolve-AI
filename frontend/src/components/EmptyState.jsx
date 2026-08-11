import React from 'react';
import { Bot } from 'lucide-react';

const SUGGESTIONS = [
  "Search web for latest AI news",
  "Send an email to support@example.com",
  "Calculate 15% tip on $184.50",
  "Where is my order ORD123?"
];


export default function EmptyState({ onSelectPrompt }) {
  return (
    <div className="empty-landing">
      <div className="landing-icon-badge">
        <Bot size={24} />
      </div>
      <h2 className="landing-title">Resolve AI</h2>
      <p className="landing-subtitle">How can I help you today?</p>

      <div className="suggestion-chips-grid">
        {SUGGESTIONS.map((prompt, idx) => (
          <button
            key={idx}
            className="suggestion-chip"
            onClick={() => onSelectPrompt(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

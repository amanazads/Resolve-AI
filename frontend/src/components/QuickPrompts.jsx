import React from 'react';

const SAMPLE_PROMPTS = [
  "Can I get a refund if I cancel my order within 30 days?",
  "Where is my order ORD123 right now?",
  "Please cancel my order ORD456.",
  "What is the battery life of Product A headphones?",
  "Why was my credit card declined during payment?",
  "I want to speak to a human customer support agent."
];

export default function QuickPrompts({ onSelectPrompt, disabled }) {
  return (
    <div className="quick-prompts-bar">
      {SAMPLE_PROMPTS.map((promptText, index) => (
        <button
          key={index}
          className="quick-chip"
          onClick={() => onSelectPrompt(promptText)}
          disabled={disabled}
        >
          {promptText}
        </button>
      ))}
    </div>
  );
}

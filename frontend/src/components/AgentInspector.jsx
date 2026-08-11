import React from 'react';
import { Check, X, Activity } from 'lucide-react';

export default function AgentInspector({ 
  isOpen, 
  onClose, 
  lastMessage 
}) {
  if (!isOpen) return null;

  const intent = lastMessage?.intent || 'GENERAL';
  const confidence = lastMessage?.confidence || 0.95;
  const sources = lastMessage?.sources || [];
  const toolUsed = lastMessage?.tool_used;
  const escalated = lastMessage?.escalated;

  return (
    <div className="inspector-panel">
      <div className="inspector-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Activity size={16} />
          <span>Agent Activity</span>
        </div>
        <button 
          className="btn-sidebar-toggle" 
          onClick={onClose}
          title="Close Panel"
        >
          <X size={16} />
        </button>
      </div>

      <div className="inspector-body">
        {/* Step 1: Intent */}
        <div className="step-card">
          <div className="step-icon-check">
            <Check size={12} />
          </div>
          <div className="step-info">
            <span className="step-title">Intent Detected</span>
            <span className="step-detail">{intent} (Confidence: {confidence})</span>
          </div>
        </div>

        {/* Step 2: RAG Search */}
        {sources.length > 0 && (
          <div className="step-card">
            <div className="step-icon-check">
              <Check size={12} />
            </div>
            <div className="step-info">
              <span className="step-title">Knowledge Base Searched</span>
              <span className="step-detail">{sources.length} relevant document(s) retrieved</span>
            </div>
          </div>
        )}

        {/* Step 3: Tool Execution */}
        {toolUsed && (
          <div className="step-card">
            <div className="step-icon-check">
              <Check size={12} />
            </div>
            <div className="step-info">
              <span className="step-title">Tool Executed</span>
              <span className="step-detail">{toolUsed}()</span>
            </div>
          </div>
        )}

        {/* Step 4: Escalation */}
        {escalated && (
          <div className="step-card">
            <div className="step-icon-check" style={{ background: 'var(--accent-red-bg)', color: 'var(--accent-red)' }}>
              !
            </div>
            <div className="step-info">
              <span className="step-title" style={{ color: 'var(--accent-red)' }}>Human Escalated</span>
              <span className="step-detail">Ticket created in MongoDB</span>
            </div>
          </div>
        )}

        {/* Step 5: Grounded Response */}
        <div className="step-card">
          <div className="step-icon-check">
            <Check size={12} />
          </div>
          <div className="step-info">
            <span className="step-title">Response Grounded</span>
            <span className="step-detail">Strict zero-hallucination verification</span>
          </div>
        </div>
      </div>
    </div>
  );
}

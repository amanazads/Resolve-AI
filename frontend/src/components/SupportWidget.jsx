import React, { useState } from 'react';
import { Bot, X, Send } from 'lucide-react';
import ChatMessage from './ChatMessage';

export default function SupportWidget({ 
  messages, 
  onSendMessage, 
  loading, 
  onSpeakMessage,
  isSpeaking,
  onStopSpeaking
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [text, setText] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (text.trim() && !loading) {
      onSendMessage(text.trim());
      setText('');
    }
  };

  return (
    <div className="widget-mode-container">
      {/* Widget Window */}
      {isOpen && (
        <div className="widget-popup-window">
          <div className="header" style={{ height: 48, padding: '0 14px' }}>
            <div className="header-left">
              <div className="brand-icon-box" style={{ width: 22, height: 22, fontSize: '0.7rem' }}>
                R
              </div>
              <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>Resolve AI</span>
            </div>
            <button 
              className="btn-sidebar-toggle"
              onClick={() => setIsOpen(false)}
            >
              <X size={15} />
            </button>
          </div>

          <div className="chat-viewport" style={{ padding: 12 }}>
            <div className="messages-wrapper" style={{ gap: 12 }}>
              {messages.map((msg, idx) => (
                <ChatMessage 
                  key={idx} 
                  message={msg} 
                  onSpeakMessage={onSpeakMessage}
                  isSpeaking={isSpeaking}
                  onStopSpeaking={onStopSpeaking}
                />
              ))}


              {loading && (
                <div className="thinking-card">
                  <div className="pulsing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  Thinking...
                </div>
              )}
            </div>
          </div>

          <form onSubmit={handleSubmit} className="composer-area" style={{ padding: '8px 12px 12px' }}>
            <div className="composer-box" style={{ padding: '4px 6px 4px 10px' }}>
              <input
                type="text"
                className="composer-input"
                placeholder="Ask support..."
                value={text}
                onChange={(e) => setText(e.target.value)}
                style={{ fontSize: '0.82rem' }}
              />
              <button type="submit" className="btn-send" disabled={!text.trim() || loading} style={{ padding: '6px 10px' }}>
                <Send size={14} />
              </button>
            </div>
          </form>
        </div>
      )}

      {/* FAB Button */}
      <button 
        className="widget-fab-button"
        onClick={() => setIsOpen(!isOpen)}
        title="Open Support Widget"
      >
        {isOpen ? <X size={22} /> : <Bot size={22} />}
      </button>
    </div>
  );
}

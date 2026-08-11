import React, { useState, useEffect } from 'react';
import { Send, Mic, MicOff } from 'lucide-react';

export default function ChatInput({ 
  onSendMessage, 
  disabled,
  isListening,
  startListening,
  stopListening,
  transcript
}) {
  const [text, setText] = useState('');

  useEffect(() => {
    if (transcript) {
      setText(transcript);
    }
  }, [transcript]);

  const handleSubmit = (e) => {
    e?.preventDefault();
    if (isListening) {
      stopListening();
    }
    if (text.trim() && !disabled) {
      onSendMessage(text.trim(), false);
      setText('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      handleSubmit(e);
    }
  };

  const toggleMic = () => {
    if (isListening) {
      stopListening();
    } else {
      startListening((finalText) => {
        if (finalText.trim()) {
          onSendMessage(finalText.trim(), true);
          setText('');
        }
      });
    }
  };


  return (
    <footer className="composer-area">
      <form onSubmit={handleSubmit} className="composer-box">
        <button
          type="button"
          className={`btn-mic ${isListening ? 'listening' : ''}`}
          onClick={toggleMic}
          disabled={disabled}
          title={isListening ? "Listening... Click to stop" : "Speak voice command"}
        >
          {isListening ? <MicOff size={16} /> : <Mic size={16} />}
        </button>

        <input
          type="text"
          className="composer-input"
          placeholder={
            isListening
              ? "Listening... Speak now..."
              : "Ask anything about orders, policies, products..."
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
        />

        <button type="submit" className="btn-send" disabled={!text.trim() || disabled}>
          <Send size={14} />
        </button>
      </form>
    </footer>
  );
}

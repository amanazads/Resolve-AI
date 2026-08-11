import React, { useState, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import EmptyState from './components/EmptyState';
import ChatMessage from './components/ChatMessage';
import ChatInput from './components/ChatInput';
import AgentInspector from './components/AgentInspector';
import SupportWidget from './components/SupportWidget';
import { sendChatMessage, escalateSession } from './services/api';
import { useVoice } from './hooks/useVoice';

const generateSessionId = () => 'sess_' + Math.random().toString(36).substring(2, 9);
const USER_ID = 'user123';

export default function App() {
  const [sessionId, setSessionId] = useState(generateSessionId());
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isEscalated, setIsEscalated] = useState(false);
  
  // Layout states
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [isWidgetMode, setIsWidgetMode] = useState(false);

  const messagesEndRef = useRef(null);

  const {
    lang,
    setLang,
    isListening,
    isSpeaking,
    autoSpeak,
    setAutoSpeak,
    handsFreeMode,
    setHandsFreeMode,
    transcript,
    startListening,
    stopListening,
    speak,
    stopSpeaking
  } = useVoice();

  const [activeSpeechIdx, setActiveSpeechIdx] = useState(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  const handleToggleSpeak = (text, idx) => {
    if (activeSpeechIdx === idx && isSpeaking) {
      stopSpeaking();
      setActiveSpeechIdx(null);
    } else {
      stopSpeaking();
      setActiveSpeechIdx(idx);
      speak(text, lang, () => {
        setActiveSpeechIdx(null);
      });
    }
  };

  const handleSendMessage = async (text) => {
    const userMsg = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const data = await sendChatMessage(sessionId, USER_ID, text);
      const assistantMsg = {
        role: 'assistant',
        content: data.response,
        intent: data.intent,
        confidence: data.confidence,
        sources: data.sources || [],
        tool_used: data.tool_used,
        tool_result: data.tool_result,
        escalated: data.escalated
      };

      setMessages((prev) => [...prev, assistantMsg]);
      if (data.escalated) {
        setIsEscalated(true);
      }

      // Voice output ONLY plays if user has explicitly enabled autoSpeak toggle
      if (autoSpeak && data.response) {
        setActiveSpeechIdx(messages.length + 1);
        speak(data.response, lang, () => {
          setActiveSpeechIdx(null);
        });
      }
    } catch (err) {
      console.error('API Error:', err);
      const errMsg = 'Sorry, an error occurred while connecting to the customer support backend service.';
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: errMsg,
          intent: 'ERROR'
        }
      ]);
    } finally {
      setLoading(false);
    }
  };


  const handleManualEscalate = async () => {
    if (isEscalated) return;
    try {
      await escalateSession(sessionId, USER_ID, 'User requested human support');
      setIsEscalated(true);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'I have marked this conversation for human escalation. A support representative will be with you shortly.',
          escalated: true
        }
      ]);
    } catch (err) {
      console.error('Escalation failed:', err);
    }
  };

  const handleNewSession = () => {
    stopSpeaking();
    stopListening();
    setSessionId(`session_${Date.now()}`);
    setIsEscalated(false);
    setMessages([]);
    setActiveSpeechIdx(null);
  };

  // If in Floating Widget Mode demo
  if (isWidgetMode) {
    return (
      <div className="app-frame">
        <Header
          sidebarOpen={false}
          onToggleSidebar={() => {}}
          inspectorOpen={false}
          onToggleInspector={() => {}}
          isEscalated={isEscalated}
          onEscalate={handleManualEscalate}
          lang={lang}
          onLangChange={setLang}
          autoSpeak={autoSpeak}
          onToggleAutoSpeak={() => setAutoSpeak(!autoSpeak)}
          handsFreeMode={handsFreeMode}
          onToggleHandsFree={() => setHandsFreeMode(!handsFreeMode)}
          isWidgetMode={isWidgetMode}
          onToggleWidgetMode={() => setIsWidgetMode(false)}
        />
        <div style={{ padding: 40, color: 'var(--text-secondary)' }}>
          <h3>Embeddable Support Widget Demo</h3>
          <p>Look at the bottom right corner of the page to interact with the floating support widget!</p>
        </div>
        <SupportWidget 
          messages={messages}
          onSendMessage={handleSendMessage}
          loading={loading}
          onSpeakMessage={(txt) => speak(txt, lang)}
          isSpeaking={isSpeaking}
          onStopSpeaking={stopSpeaking}
        />

      </div>
    );
  }

  const lastAssistantMsg = [...messages].reverse().find(m => m.role === 'assistant');

  return (
    <div className="app-frame">
      <Sidebar 
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        onNewChat={handleNewSession}
        sessionId={sessionId}
      />

      <main className="main-chat-container">
        <Header 
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(true)}
          inspectorOpen={inspectorOpen}
          onToggleInspector={() => setInspectorOpen(!inspectorOpen)}
          isEscalated={isEscalated}
          onEscalate={handleManualEscalate}
          lang={lang}
          onLangChange={setLang}
          autoSpeak={autoSpeak}
          onToggleAutoSpeak={() => setAutoSpeak(!autoSpeak)}
          handsFreeMode={handsFreeMode}
          onToggleHandsFree={() => setHandsFreeMode(!handsFreeMode)}
          isWidgetMode={isWidgetMode}
          onToggleWidgetMode={() => setIsWidgetMode(true)}
        />


        <div className="chat-viewport">
          {messages.length === 0 ? (
            <EmptyState onSelectPrompt={handleSendMessage} />
          ) : (
            <div className="messages-wrapper">
              {messages.map((msg, idx) => (
                <ChatMessage 
                  key={idx}
                  message={msg}
                  onSpeakMessage={(txt) => handleToggleSpeak(txt, idx)}
                  isSpeaking={isSpeaking && activeSpeechIdx === idx}
                  onStopSpeaking={() => {
                    stopSpeaking();
                    setActiveSpeechIdx(null);
                  }}
                />
              ))}



              {loading && (
                <div className="thinking-card">
                  <div className="pulsing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <span>Searching knowledge & analyzing request...</span>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <ChatInput 
          onSendMessage={handleSendMessage}
          disabled={loading}
          isListening={isListening}
          startListening={startListening}
          stopListening={stopListening}
          transcript={transcript}
        />
      </main>

      <AgentInspector 
        isOpen={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
        lastMessage={lastAssistantMsg}
      />
    </div>
  );
}

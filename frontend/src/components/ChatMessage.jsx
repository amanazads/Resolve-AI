import React, { useState } from 'react';
import { Bot, User, FileText, Zap, ChevronDown, ChevronUp, AlertTriangle, Volume2, VolumeX, ExternalLink, Globe, Mail } from 'lucide-react';


function parseLinksAndEmails(str) {
  if (!str) return null;

  // Regex matching Markdown Links [text](url), Raw URLs (http://, https://, www.), and Email addresses (name@domain.com)
  const combinedRegex = /(\[([^\]]+)\]\(([^)]+)\))|(https?:\/\/[^\s<]+|www\.[^\s<]+)|([\w\.-]+@[\w\.-]+\.\w+)/g;
  
  let match;
  let lastIdx = 0;
  const elements = [];

  while ((match = combinedRegex.exec(str)) !== null) {
    if (match.index > lastIdx) {
      elements.push(str.slice(lastIdx, match.index));
    }

    if (match[1]) {
      // 1. Markdown link [text](url)
      const label = match[2];
      let targetUrl = match[3];
      if (targetUrl.includes('@') && !targetUrl.startsWith('mailto:') && !targetUrl.startsWith('http')) {
        targetUrl = 'mailto:' + targetUrl;
      }
      elements.push(
        <a
          key={match.index}
          href={targetUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="chat-interactive-link"
        >
          {label} <ExternalLink size={10} />
        </a>
      );
    } else if (match[4]) {
      // 2. Raw URL (http://, https://, www.)
      let rawUrl = match[4];
      const hrefUrl = rawUrl.startsWith('www.') ? 'https://' + rawUrl : rawUrl;
      elements.push(
        <a
          key={match.index}
          href={hrefUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="chat-interactive-link"
        >
          {rawUrl} <ExternalLink size={10} />
        </a>
      );
    } else if (match[5]) {
      // 3. Raw Email Address (name@domain.com)
      const email = match[5];
      elements.push(
        <a
          key={match.index}
          href={`mailto:${email}`}
          target="_blank"
          rel="noopener noreferrer"
          className="chat-interactive-link email"
        >
          <Mail size={11} /> {email}
        </a>
      );
    }

    lastIdx = match.index + match[0].length;
  }

  if (lastIdx < str.length) {
    elements.push(str.slice(lastIdx));
  }

  return elements;
}

function renderTextWithLinksAndBold(text) {
  if (!text) return null;
  
  // Split by markdown bold **text**
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
          {parseLinksAndEmails(part.slice(2, -2))}
        </strong>
      );
    }
    return <React.Fragment key={i}>{parseLinksAndEmails(part)}</React.Fragment>;
  });
}

function FormattedContent({ content }) {
  if (!content) return null;

  const lines = content.split('\n');
  return (
    <div className="formatted-text-block">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} style={{ height: 6 }} />;

        if (trimmed.startsWith('- ') || trimmed.startsWith('• ') || trimmed.startsWith('* ')) {
          const itemText = trimmed.replace(/^[-•*]\s*/, '');
          return (
            <div key={idx} className="formatted-list-item">
              <span className="list-bullet">•</span>
              <span>{renderTextWithLinksAndBold(itemText)}</span>
            </div>
          );
        }

        if (trimmed.startsWith('### ') || trimmed.startsWith('## ')) {
          const headingText = trimmed.replace(/^#+\s*/, '');
          return (
            <h4 key={idx} className="formatted-heading">
              {headingText}
            </h4>
          );
        }

        return (
          <p key={idx} className="formatted-paragraph">
            {renderTextWithLinksAndBold(trimmed)}
          </p>
        );
      })}
    </div>
  );
}

export default function ChatMessage({ 
  message, 
  onSpeakMessage,
  isSpeaking,
  onStopSpeaking
}) {
  const isUser = message.role === 'user';
  const [showSources, setShowSources] = useState(false);
  const [showTool, setShowTool] = useState(false);

  const sources = message.sources || [];
  const toolUsed = message.tool_used;
  const toolResult = message.tool_result;
  const isEscalated = message.escalated;

  // Extract domain name from URL
  const getDomain = (url) => {
    try {
      const parsed = new URL(url);
      return parsed.hostname.replace(/^www\./, '');
    } catch {
      return url;
    }
  };

  const handleSpeakerClick = () => {
    if (isSpeaking) {
      if (onStopSpeaking) onStopSpeaking();
    } else {
      if (onSpeakMessage) onSpeakMessage(message.content);
    }
  };

  return (
    <div className={`message-row ${isUser ? 'user' : 'assistant'}`}>
      <div className="msg-avatar-icon">
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>

      <div className="msg-body-card">
        <div className="msg-bubble">
          <div className="msg-text-header">
            <div className="msg-text-content">
              <FormattedContent content={message.content} />
            </div>

            {!isUser && (onSpeakMessage || onStopSpeaking) && (
              <button 
                className={`btn-speak ${isSpeaking ? 'speaking-active' : ''}`}
                onClick={handleSpeakerClick}
                title={isSpeaking ? "Click to stop speaking" : "Speak response out loud"}
              >
                {isSpeaking ? (
                  <VolumeX size={14} style={{ color: '#ef4444' }} />
                ) : (
                  <Volume2 size={14} />
                )}
              </button>
            )}
          </div>
        </div>


        {!isUser && (
          <>
            {/* RAG Expandable Sources */}
            {sources.length > 0 && (
              <div className="rag-sources-expander">
                <button 
                  className="btn-source-toggle"
                  onClick={() => setShowSources(!showSources)}
                >
                  <FileText size={12} />
                  <span>Sources · {sources.length}</span>
                  {showSources ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </button>

                {showSources && (
                  <div className="sources-dropdown">
                    {sources.map((src, idx) => (
                      <div key={idx} className="source-item-card">
                        {src.startsWith('http') ? (
                          <a href={src} target="_blank" rel="noopener noreferrer" className="source-link-chip">
                            <Globe size={11} />
                            <span>{getDomain(src)}</span>
                            <ExternalLink size={10} />
                          </a>
                        ) : (
                          <span className="source-file-badge">{src}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Custom Tool Call Result Card */}
            {toolUsed && (
              <div className="tool-call-expander">
                <button 
                  className="btn-tool-toggle"
                  onClick={() => setShowTool(!showTool)}
                >
                  <Zap size={12} />
                  <span>
                    {toolUsed === 'web_search' && '🌐 Live Web Search Executed'}
                    {toolUsed === 'send_email' && '📧 Email Composed & Sent'}
                    {toolUsed === 'make_phone_call' && '📞 Phone Call Dispatched'}
                    {toolUsed === 'execute_python_calc' && '🐍 Math / Python Evaluated'}
                    {!['web_search', 'send_email', 'make_phone_call', 'execute_python_calc'].includes(toolUsed) && `⚡ Tool Executed (${toolUsed})`}
                  </span>
                  {showTool ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </button>

                {showTool && toolResult && (
                  <div className="tool-card-box">
                    <div className="tool-card-title">
                      <Zap size={13} style={{ color: 'var(--accent-green)' }} />
                      <span>{toolUsed} Output</span>
                    </div>

                    {toolUsed === 'web_search' && toolResult.results ? (
                      <div className="web-search-cards-list">
                        {toolResult.results.map((res, idx) => (
                          <div key={idx} className="web-result-card">
                            <div className="web-result-header">
                              <span className="web-domain-badge">{getDomain(res.url)}</span>
                              <a href={res.url} target="_blank" rel="noopener noreferrer" className="web-result-link">
                                <ExternalLink size={11} />
                              </a>
                            </div>
                            <h5 className="web-result-title">{res.title}</h5>
                            <p className="web-result-snippet">{res.snippet}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="tool-kv-grid">
                        {typeof toolResult === 'object' ? (
                          Object.entries(toolResult).map(([k, v]) => (
                            <React.Fragment key={k}>
                              <span className="tool-kv-key">{k}:</span>
                              <span className="tool-kv-val">
                                {typeof v === 'object' ? (
                                  JSON.stringify(v)
                                ) : (
                                  renderTextWithLinksAndBold(String(v))
                                )}
                              </span>
                            </React.Fragment>
                          ))
                        ) : (
                          <span className="tool-kv-val">{renderTextWithLinksAndBold(String(toolResult))}</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Human Escalation Alert */}
            {isEscalated && (
              <div className="escalation-alert-card">
                <AlertTriangle size={15} style={{ flexShrink: 0 }} />
                <div>
                  <strong>Human Support Requested</strong> — This conversation has been marked for manual support.
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

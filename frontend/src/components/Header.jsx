import React from 'react';
import { PanelLeft, Activity, Volume2, VolumeX, AlertTriangle, Globe, Layers, Radio } from 'lucide-react';
import { SUPPORTED_LANGUAGES } from '../hooks/useVoice';

export default function Header({ 
  sidebarOpen,
  onToggleSidebar,
  inspectorOpen,
  onToggleInspector,
  isEscalated,
  onEscalate,
  lang,
  onLangChange,
  autoSpeak,
  onToggleAutoSpeak,
  handsFreeMode,
  onToggleHandsFree,
  isWidgetMode,
  onToggleWidgetMode
}) {
  return (
    <header className="header">
      <div className="header-left">
        {!sidebarOpen && (
          <button 
            className="btn-sidebar-toggle"
            onClick={onToggleSidebar}
            title="Open Sidebar"
          >
            <PanelLeft size={16} />
          </button>
        )}

        <div className="header-title">
          <span>Resolve AI</span>
          <div className={`status-indicator ${isEscalated ? 'escalated' : ''}`}>
            <span className="status-dot"></span>
            <span>{isEscalated ? 'Escalated' : 'Online'}</span>
          </div>
        </div>
      </div>

      <div className="header-right">
        {/* Language Selector */}
        <div className="language-selector-compact">
          <Globe size={13} style={{ color: 'var(--text-muted)' }} />
          <select 
            value={lang} 
            onChange={(e) => onLangChange(e.target.value)}
            className="lang-select-clean"
          >
            {SUPPORTED_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.flag} {l.name}
              </option>
            ))}
          </select>
        </div>

        {/* Auto-speak Toggle */}
        <button
          className={`btn-header ${autoSpeak ? 'active' : ''}`}
          onClick={onToggleAutoSpeak}
          title={autoSpeak ? "Agent Voice Output: ON (Speaking)" : "Agent Voice Output: OFF (Written Mode Only)"}
        >
          {autoSpeak ? <Volume2 size={14} /> : <VolumeX size={14} />}
          <span>{autoSpeak ? 'Voice ON' : 'Written Only'}</span>
        </button>



        {/* Inspector Panel Toggle */}
        <button
          className={`btn-header ${inspectorOpen ? 'active' : ''}`}
          onClick={onToggleInspector}
          title="Toggle Agent Activity Inspector"
        >
          <Activity size={14} />
          <span>Inspector</span>
        </button>

        {/* Widget Mode Toggle */}
        <button
          className={`btn-header ${isWidgetMode ? 'active' : ''}`}
          onClick={onToggleWidgetMode}
          title="Toggle Embeddable Widget Demo Mode"
        >
          <Layers size={14} />
          <span>{isWidgetMode ? 'Widget Demo' : 'Full App'}</span>
        </button>

        {/* Request Human Button */}
        <button 
          className="btn-header escalate" 
          onClick={onEscalate}
          disabled={isEscalated}
          title="Request Human Agent"
        >
          <AlertTriangle size={14} />
          <span>{isEscalated ? 'Escalated' : 'Request Human'}</span>
        </button>
      </div>
    </header>
  );
}

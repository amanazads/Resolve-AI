import React, { useState, useEffect } from 'react';
import {
  Sparkles,
  PlusCircle,
  ListTodo,
  Activity,
  Plug,
  Settings as SettingsIcon,
  MessageSquare,
  User
} from 'lucide-react';
import { useRoute, navigate } from '../../router';
import { getCurrentUser } from '../../services/api';
import TaskWorkspace from './TaskWorkspace';
import TasksList from './TasksList';
import Connections from './Connections';
import Settings from './Settings';
import ActivityLog from './ActivityLog';

const NAV = [
  { key: 'new', label: 'New Task', icon: PlusCircle, path: '/new' },
  { key: 'tasks', label: 'Tasks', icon: ListTodo, path: '/tasks' },
  { key: 'activity', label: 'Activity', icon: Activity, path: '/activity' },
  { key: 'connections', label: 'Connections', icon: Plug, path: '/connections' },
  { key: 'settings', label: 'Settings', icon: SettingsIcon, path: '/settings' }
];

export default function WorkspaceShell({ onOpenAssistant }) {
  const route = useRoute();
  const [currentUser, setCurrentUser] = useState(getCurrentUser());

  useEffect(() => {
    setCurrentUser(getCurrentUser());
  }, [route]);

  const renderScreen = () => {
    switch (route.screen) {
      case 'new':
        return <TaskWorkspace initialTaskId={null} />;
      case 'tasks':
        return route.param ? (
          <TaskWorkspace initialTaskId={route.param} />
        ) : (
          <TasksList />
        );
      case 'activity':
        return <ActivityLog campaignId={route.query.campaign || null} />;
      case 'connections':
      case 'integrations':
        return <Connections />;
      case 'settings':
        return <Settings />;
      default:
        return <TaskWorkspace initialTaskId={null} />;
    }
  };

  const isNavActive = (key) => {
    if (key === 'new' && route.screen === 'new') return true;
    if (key === 'tasks' && (route.screen === 'tasks' || !route.screen)) return true;
    if (key === 'activity' && route.screen === 'activity') return true;
    if (key === 'connections' && (route.screen === 'connections' || route.screen === 'integrations')) return true;
    if (key === 'settings' && route.screen === 'settings') return true;
    return false;
  };

  return (
    <div className="ws-shell">
      <nav className="ws-rail" aria-label="Workspace">
        {/* Brand header */}
        <div className="ws-rail-brand" onClick={() => navigate('/new')} style={{ cursor: 'pointer' }}>
          <div className="brand-icon-box">
            <span>R</span>
          </div>
          <div className="ws-rail-brand-text">
            <strong>Resolve AI</strong>
            <span>Autonomous Agent</span>
          </div>
        </div>

        {/* Primary navigation */}
        <div className="ws-rail-nav">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = isNavActive(item.key);
            return (
              <button
                key={item.key}
                className={`ws-rail-item ${active ? 'active' : ''}`}
                onClick={() => navigate(item.path)}
                aria-current={active ? 'page' : undefined}
                style={item.key === 'new' ? { fontWeight: 600, color: active ? '#fff' : 'var(--accent-brand)' } : undefined}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="ws-rail-footer" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* User profile badge */}
          <div
            onClick={() => navigate('/settings')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '8px 10px',
              background: 'rgba(255,255,255,0.03)',
              borderRadius: '6px',
              cursor: 'pointer',
              border: '1px solid var(--border-subtle)'
            }}
          >
            <div
              style={{
                width: '24px',
                height: '24px',
                borderRadius: '50%',
                background: 'rgba(99, 102, 241, 0.2)',
                color: 'var(--accent-brand)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '11px',
                fontWeight: 600
              }}
            >
              <User size={13} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: '#fff', textOverflow: 'ellipsis', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                {currentUser.name || 'Aman Azad'}
              </span>
              <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {currentUser.user_id}
              </span>
            </div>
          </div>

          <button className="ws-rail-item" onClick={onOpenAssistant} style={{ fontSize: '12px' }}>
            <MessageSquare size={14} />
            <span>Support Chat</span>
          </button>
        </div>
      </nav>

      <main className="ws-main">{renderScreen()}</main>
    </div>
  );
}

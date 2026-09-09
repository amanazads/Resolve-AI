import React, { useState, useEffect } from 'react';
import {
  PlusCircle,
  ListTodo,
  Activity,
  Plug,
  Settings as SettingsIcon,
  MessageSquare,
  User,
  ShieldCheck,
  CheckCircle2
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
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100vh', background: 'var(--bg-main)', overflow: 'hidden' }}>
      {/* Top Workspace Header Bar */}
      <header
        style={{
          height: '52px',
          background: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          flexShrink: 0,
          zIndex: 30
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', cursor: 'pointer' }} onClick={() => navigate('/new')}>
          <div
            style={{
              width: '28px',
              height: '28px',
              borderRadius: '6px',
              background: 'linear-gradient(135deg, #6366f1, #4f46e5)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 700,
              fontSize: '14px',
              boxShadow: '0 2px 8px rgba(99, 102, 241, 0.4)'
            }}
          >
            <span>R</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <strong style={{ fontSize: '15px', fontWeight: 600, color: '#fff', letterSpacing: '-0.01em' }}>Resolve AI</strong>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Autonomous AI Execution Workspace</span>
          </div>
        </div>

        {/* User Identity & System Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div
            onClick={() => navigate('/settings')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '4px 10px',
              background: 'rgba(255,255,255,0.03)',
              borderRadius: '20px',
              border: '1px solid var(--border-subtle)',
              cursor: 'pointer'
            }}
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '50%', background: 'rgba(99, 102, 241, 0.2)', color: 'var(--accent-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 600 }}>
              <User size={12} />
            </div>
            <span style={{ fontSize: '12px', fontWeight: 500, color: '#fff' }}>
              {currentUser.name || 'Aman Azad'}
            </span>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '11px',
                color: '#10b981',
                background: 'rgba(16, 185, 129, 0.1)',
                padding: '2px 8px',
                borderRadius: '10px',
                border: '1px solid rgba(16, 185, 129, 0.25)'
              }}
            >
              <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#10b981', display: 'inline-block', boxShadow: '0 0 6px #10b981' }}></span>
              Online
            </span>
          </div>
        </div>
      </header>

      {/* Main Workspace Frame */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Navigation Rail */}
        <nav
          className="ws-rail"
          aria-label="Workspace"
          style={{
            width: '220px',
            flexShrink: 0,
            background: 'var(--bg-surface)',
            borderRight: '1px solid var(--border-subtle)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            padding: '16px 12px'
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {/* Primary Action Button */}
            <button
              onClick={() => navigate('/new')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                width: '100%',
                padding: '10px 14px',
                background: route.screen === 'new' ? 'linear-gradient(135deg, #6366f1, #4f46e5)' : 'rgba(99, 102, 241, 0.12)',
                color: route.screen === 'new' ? '#fff' : 'var(--accent-brand)',
                border: route.screen === 'new' ? '1px solid #6366f1' : '1px solid rgba(99, 102, 241, 0.3)',
                borderRadius: '8px',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
                marginBottom: '16px',
                transition: 'all 0.15s ease',
                boxShadow: route.screen === 'new' ? '0 2px 10px rgba(99, 102, 241, 0.3)' : 'none'
              }}
            >
              <PlusCircle size={16} />
              <span>New Task</span>
            </button>

            {/* Standard Nav Items */}
            {NAV.filter((item) => item.key !== 'new').map((item) => {
              const Icon = item.icon;
              const active = isNavActive(item.key);
              return (
                <button
                  key={item.key}
                  className={`ws-rail-item ${active ? 'active' : ''}`}
                  onClick={() => navigate(item.path)}
                  aria-current={active ? 'page' : undefined}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    fontSize: '13px',
                    background: active ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
                    color: active ? '#fff' : 'var(--text-secondary)',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'left',
                    fontWeight: active ? 600 : 400
                  }}
                >
                  <Icon size={15} color={active ? '#fff' : 'var(--text-muted)'} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>

          {/* Footer Controls */}
          <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <button
              onClick={onOpenAssistant}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                width: '100%',
                padding: '8px 12px',
                borderRadius: '6px',
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                fontSize: '12px',
                cursor: 'pointer',
                textAlign: 'left'
              }}
            >
              <MessageSquare size={14} />
              <span>Support Chat</span>
            </button>
          </div>
        </nav>

        {/* Main Content Viewport */}
        <main
          className="ws-main"
          style={{
            flex: 1,
            overflowY: 'auto',
            background: 'var(--bg-main)',
            padding: '24px 32px'
          }}
        >
          {renderScreen()}
        </main>
      </div>
    </div>
  );
}

import React from 'react';
import {
  Activity,
  LayoutDashboard,
  MessageSquare,
  Plug,
  Send,
  Sparkles,
  Table2
} from 'lucide-react';
import { useRoute, navigate } from '../../router';
import TaskWorkspace from './TaskWorkspace';
import Dashboard from './Dashboard';
import Campaigns from './Campaigns';
import CreateCampaign from './CreateCampaign';
import DatasetUpload from './DatasetUpload';
import DatasetPreview from './DatasetPreview';
import CampaignDetails from './CampaignDetails';
import Integrations from './Integrations';
import ActivityLog from './ActivityLog';

const NAV = [
  { key: 'tasks', label: 'Tasks', icon: Sparkles, path: '/tasks' },
  { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, path: '/dashboard' },
  { key: 'campaigns', label: 'Campaigns', icon: Send, path: '/campaigns' },
  { key: 'datasets', label: 'Contacts', icon: Table2, path: '/datasets' },
  { key: 'integrations', label: 'Integrations', icon: Plug, path: '/integrations' },
  { key: 'activity', label: 'Activity', icon: Activity, path: '/activity' }
];

/**
 * The workspace frame: navigation on the left, the routed screen on the right.
 *
 * The support assistant is not replaced by any of this -- it is one more
 * destination, reached from the same rail.
 */
export default function WorkspaceShell({ onOpenAssistant }) {
  const route = useRoute();

  const renderScreen = () => {
    switch (route.screen) {
      case 'tasks':
        return <TaskWorkspace initialTaskId={route.param} />;
      case 'campaigns':
        return route.param ? (
          <CampaignDetails campaignId={route.param} tab={route.subview} />
        ) : (
          <Campaigns />
        );
      case 'create':
        return <TaskWorkspace initialTaskId={route.param} />;
      case 'datasets':
        return route.param ? <DatasetPreview datasetId={route.param} /> : <DatasetUpload />;
      case 'integrations':
        return <Integrations />;
      case 'activity':
        return <ActivityLog campaignId={route.query.campaign || null} />;
      case 'dashboard':
        return <Dashboard />;
      default:
        return <TaskWorkspace />;
    }
  };

  return (
    <div className="ws-shell">
      <nav className="ws-rail" aria-label="Workspace">
        <div className="ws-rail-brand">
          <div className="brand-icon-box">
            <span>R</span>
          </div>
          <div className="ws-rail-brand-text">
            <strong>Resolve AI</strong>
            <span>Automation workspace</span>
          </div>
        </div>

        <div className="ws-rail-nav">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = route.screen === item.key;
            return (
              <button
                key={item.key}
                className={`ws-rail-item ${active ? 'active' : ''}`}
                onClick={() => navigate(item.path)}
                aria-current={active ? 'page' : undefined}
              >
                <Icon size={15} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>

        <div className="ws-rail-footer">
          <button className="ws-rail-item" onClick={onOpenAssistant}>
            <MessageSquare size={15} />
            <span>Support assistant</span>
          </button>
        </div>
      </nav>

      <main className="ws-main">{renderScreen()}</main>
    </div>
  );
}

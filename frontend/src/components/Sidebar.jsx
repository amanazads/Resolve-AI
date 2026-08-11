import React from 'react';
import { Plus, MessageSquare, PanelLeftClose, User } from 'lucide-react';

export default function Sidebar({ 
  isOpen, 
  onToggle, 
  onNewChat, 
  sessionId 
}) {
  return (
    <aside className={`sidebar ${isOpen ? '' : 'collapsed'}`}>
      <div className="sidebar-header">
        <div className="brand-logo">
          <div className="brand-icon-box">
            <span>R</span>
          </div>
          <span>Resolve AI</span>
        </div>
        <button 
          className="btn-sidebar-toggle"
          onClick={onToggle}
          title="Close Sidebar"
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      <div className="new-chat-section">
        <button className="btn-new-chat" onClick={onNewChat}>
          <Plus size={15} />
          <span>New Conversation</span>
        </button>
      </div>

      <div className="conversations-history">
        <div className="history-group-title">Today</div>
        <div className="history-item active">
          <MessageSquare size={14} />
          <span>Current Session ({sessionId.slice(-6)})</span>
        </div>
        <div className="history-item">
          <MessageSquare size={14} />
          <span>Order Status Query</span>
        </div>

        <div className="history-group-title">Yesterday</div>
        <div className="history-item">
          <MessageSquare size={14} />
          <span>Refund & Return Policy</span>
        </div>
        <div className="history-item">
          <MessageSquare size={14} />
          <span>Product Specification</span>
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="user-profile">
          <div className="user-avatar-small">
            <User size={12} />
          </div>
          <span>Customer Support</span>
        </div>
      </div>
    </aside>
  );
}

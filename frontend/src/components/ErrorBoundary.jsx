import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          height: '100vh',
          width: '100vw',
          backgroundColor: '#09090b',
          color: '#f4f4f5',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'Inter, system-ui, sans-serif',
          padding: 20,
          boxSizing: 'border-box'
        }}>
          <div style={{
            background: '#121215',
            border: '1px solid #27272a',
            borderRadius: 12,
            padding: 32,
            maxWidth: 480,
            textAlign: 'center',
            boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
          }}>
            <div style={{
              width: 48,
              height: 48,
              borderRadius: 24,
              background: 'rgba(239, 68, 68, 0.15)',
              color: '#ef4444',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px'
            }}>
              <AlertTriangle size={24} />
            </div>
            <h3 style={{ margin: '0 0 8px', fontSize: '1.2rem', fontWeight: 600 }}>System Restored</h3>
            <p style={{ margin: '0 0 20px', color: '#a1a1aa', fontSize: '0.88rem', lineHeight: 1.5 }}>
              A UI state exception was safely intercepted. Click below to continue without losing your workspace.
            </p>
            <button
              onClick={this.handleReset}

              style={{
                background: '#f4f4f5',
                color: '#09090b',
                border: 'none',
                padding: '10px 20px',
                borderRadius: 6,
                fontWeight: 600,
                fontSize: '0.85rem',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8
              }}
            >
              <RefreshCw size={14} /> Resume Session
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

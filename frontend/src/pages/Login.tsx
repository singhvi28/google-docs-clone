import { useAuth } from '../hooks/useAuth';
import { useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { FileText, Users, Shield, Zap } from 'lucide-react';
import './Login.css';

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (user) navigate('/dashboard', { replace: true });
  }, [user, navigate]);

  return (
    <div className="login-page">
      {/* Animated background */}
      <div className="login-bg">
        <div className="login-bg-orb login-bg-orb--1" />
        <div className="login-bg-orb login-bg-orb--2" />
        <div className="login-bg-orb login-bg-orb--3" />
        <div className="login-bg-grid" />
      </div>

      <div className="login-content">
        {/* Hero */}
        <div className="login-hero animate-slide-up">
          <div className="login-logo">
            <div className="login-logo-icon">
              <FileText size={32} />
            </div>
            <h1 className="login-title">
              Collab<span className="gradient-text">Docs</span>
            </h1>
          </div>
          <p className="login-subtitle">
            Real-time collaborative editing with live cursors,
            instant sync, and zero conflicts.
          </p>
        </div>

        {/* Login Card */}
        <div className="login-card glass animate-slide-up" style={{ animationDelay: '0.1s' }}>
          <button className="login-google-btn" onClick={login} id="google-login-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
            </svg>
            Continue with Google
          </button>
          <p className="login-card-note">
            We only use your Google account for authentication.
            No data is shared with third parties.
          </p>
        </div>

        {/* Features */}
        <div className="login-features animate-slide-up" style={{ animationDelay: '0.2s' }}>
          <div className="login-feature">
            <div className="login-feature-icon"><Zap size={20} /></div>
            <div>
              <h3>Real-Time CRDT Sync</h3>
              <p>Conflict-free editing powered by Yjs</p>
            </div>
          </div>
          <div className="login-feature">
            <div className="login-feature-icon"><Users size={20} /></div>
            <div>
              <h3>50 Live Editors</h3>
              <p>Collaborate with up to 50 people simultaneously</p>
            </div>
          </div>
          <div className="login-feature">
            <div className="login-feature-icon"><Shield size={20} /></div>
            <div>
              <h3>Access Control</h3>
              <p>Approve editors with a single click</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

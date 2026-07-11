import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api, type DocumentItem } from '../lib/api';
import {
  Plus, FileText, Trash2, ExternalLink, Eye,
  LogOut, Clock, Pencil, FolderOpen,
} from 'lucide-react';
import './Dashboard.css';

type Tab = 'created' | 'edited' | 'viewed';

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<Tab>('created');
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchDocs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listDocuments(activeTab);
      setDocs(data);
    } catch (e) {
      console.error('Failed to fetch docs:', e);
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const handleCreate = async () => {
    try {
      const doc = await api.createDocument();
      navigate(`/edit/${doc.edit_key}`);
    } catch (e) {
      console.error('Failed to create document:', e);
    }
  };

  const handleDelete = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this document permanently?')) return;
    try {
      await api.deleteDocument(docId);
      setDocs(prev => prev.filter(d => d.id !== docId));
    } catch (e) {
      console.error('Failed to delete:', e);
    }
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'created', label: 'Created', icon: <FolderOpen size={16} /> },
    { key: 'edited', label: 'Edited', icon: <Pencil size={16} /> },
    { key: 'viewed', label: 'Viewed', icon: <Eye size={16} /> },
  ];

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="dash-header glass">
        <div className="dash-header-left">
          <div className="dash-logo">
            <div className="dash-logo-icon"><FileText size={20} /></div>
            <span className="dash-logo-text">
              Collab<span className="gradient-text">Docs</span>
            </span>
          </div>
        </div>
        <div className="dash-header-right">
          <div className="dash-user">
            {user?.avatar_url && (
              <img src={user.avatar_url} alt="" className="dash-avatar" />
            )}
            <span className="dash-user-name">{user?.display_name}</span>
          </div>
          <button className="btn-icon" onClick={logout} title="Log out" id="logout-btn">
            <LogOut size={18} />
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="dash-main">
        <div className="dash-toolbar">
          <div className="dash-tabs">
            {tabs.map(tab => (
              <button
                key={tab.key}
                className={`dash-tab ${activeTab === tab.key ? 'active' : ''}`}
                onClick={() => setActiveTab(tab.key)}
                id={`tab-${tab.key}`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
          <button className="btn btn-primary" onClick={handleCreate} id="create-doc-btn">
            <Plus size={18} />
            New Document
          </button>
        </div>

        {/* Document Grid */}
        {loading ? (
          <div className="dash-loading">
            <div className="spinner" />
          </div>
        ) : docs.length === 0 ? (
          <div className="dash-empty animate-fade-in">
            <div className="dash-empty-icon"><FileText size={48} /></div>
            <h3>No documents yet</h3>
            <p>Create your first document to get started</p>
            <button className="btn btn-primary btn-lg" onClick={handleCreate}>
              <Plus size={20} />
              Create Document
            </button>
          </div>
        ) : (
          <div className="dash-grid">
            {docs.map((doc, i) => (
              <div
                key={doc.id}
                className="dash-doc-card card animate-fade-in"
                style={{ animationDelay: `${i * 0.05}s` }}
                onClick={() => navigate(`/edit/${doc.edit_key}`)}
                id={`doc-card-${doc.id}`}
              >
                <div className="dash-doc-header">
                  <div className="dash-doc-icon">
                    <FileText size={20} />
                  </div>
                  <div className="dash-doc-actions">
                    <button
                      className="btn-icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(
                          `${window.location.origin}/view/${doc.view_key}`
                        );
                      }}
                      title="Copy view link"
                    >
                      <Eye size={14} />
                    </button>
                    <button
                      className="btn-icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(
                          `${window.location.origin}/edit/${doc.edit_key}`
                        );
                      }}
                      title="Copy edit link"
                    >
                      <ExternalLink size={14} />
                    </button>
                    {doc.role === 'creator' && (
                      <button
                        className="btn-icon"
                        onClick={(e) => handleDelete(doc.id, e)}
                        title="Delete document"
                        id={`delete-doc-${doc.id}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
                <h3 className="dash-doc-title">{doc.title}</h3>
                <div className="dash-doc-meta">
                  <span className={`dash-doc-role dash-doc-role--${doc.role}`}>
                    {doc.role}
                  </span>
                  <span className="dash-doc-date">
                    <Clock size={12} />
                    {formatDate(doc.updated_at)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

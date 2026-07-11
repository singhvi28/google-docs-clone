const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';
const WT_URL = import.meta.env.VITE_WT_URL || '';

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  title: string;
  edit_key: string;
  view_key: string;
  role: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail {
  id: string;
  title: string;
  edit_key: string;
  view_key: string;
  creator_id: string;
  created_at: string;
  updated_at: string;
}

function getToken(): string | null {
  return localStorage.getItem('token');
}

function authHeaders(): HeadersInit {
  const token = getToken();
  return token
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    : { 'Content-Type': 'application/json' };
}

export const api = {
  // Auth
  getLoginUrl: () => `${API_URL}/api/auth/login`,

  getMe: async (): Promise<User> => {
    const res = await fetch(`${API_URL}/api/auth/me`, { headers: authHeaders() });
    if (!res.ok) throw new Error('Not authenticated');
    return res.json();
  },

  // Documents
  createDocument: async (title = 'Untitled Document'): Promise<DocumentDetail> => {
    const res = await fetch(`${API_URL}/api/documents/`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error('Failed to create document');
    return res.json();
  },

  listDocuments: async (tab = 'created'): Promise<DocumentItem[]> => {
    const res = await fetch(`${API_URL}/api/documents/?tab=${tab}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error('Failed to list documents');
    return res.json();
  },

  deleteDocument: async (docId: string): Promise<void> => {
    const res = await fetch(`${API_URL}/api/documents/${docId}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error('Failed to delete document');
  },

  updateTitle: async (docId: string, title: string): Promise<void> => {
    const res = await fetch(`${API_URL}/api/documents/${docId}/title`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error('Failed to update title');
  },

  getDocByEditKey: async (editKey: string): Promise<DocumentDetail> => {
    const res = await fetch(`${API_URL}/api/documents/by-edit-key/${editKey}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error('Document not found');
    return res.json();
  },

  getDocByViewKey: async (viewKey: string): Promise<DocumentDetail> => {
    const res = await fetch(`${API_URL}/api/documents/by-view-key/${viewKey}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error('Document not found');
    return res.json();
  },

  // WebSocket URL (TCP fallback)
  getWsUrl: (editKey: string): string => {
    const token = getToken();
    return `${WS_URL}/ws/doc/${editKey}?token=${token}`;
  },

  // WebTransport URL (QUIC) — null when not configured
  getWtUrl: (editKey: string): string | null => {
    if (!WT_URL) return null;
    const token = getToken();
    return `${WT_URL}/wt/doc/${editKey}?token=${token}`;
  },

  // SSE Viewer URL
  getViewerStreamUrl: (viewKey: string): string => {
    return `${API_URL}/api/view/${viewKey}/stream`;
  },
};

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import Highlight from '@tiptap/extension-highlight';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import { TextStyle } from '@tiptap/extension-text-style';
import { Color } from '@tiptap/extension-color';
import Collaboration from '@tiptap/extension-collaboration';
import * as Y from 'yjs';
import {
  Awareness,
  applyAwarenessUpdate,
  encodeAwarenessUpdate,
} from 'y-protocols/awareness';
import { api } from '../lib/api';
import { decodeYjsUpdate, encodeYjsUpdate } from '../lib/yjsSync';
import { CollaborationCursor } from '../lib/collaborationCursor';
import Toolbar from '../components/Toolbar';
import ApprovalPopup from '../components/ApprovalPopup';
import {
  ArrowLeft, Users, Wifi, WifiOff, Share2, Eye, Copy, Check,
} from 'lucide-react';
import './Editor.css';

export default function Editor() {
  const { editKey } = useParams<{ editKey: string }>();
  const navigate = useNavigate();
  const [docTitle, setDocTitle] = useState('Untitled Document');
  const [docId, setDocId] = useState('');
  const [viewKey, setViewKey] = useState('');
  const [connected, setConnected] = useState(false);
  const [editorCount, setEditorCount] = useState(1);
  const [showShare, setShowShare] = useState(false);
  const [copied, setCopied] = useState('');
  const [approvalRequests, setApprovalRequests] = useState<
    { user_id: string; moniker: string }[]
  >([]);
  const ydoc = useMemo(() => new Y.Doc(), [editKey]);
  const awareness = useMemo(() => new Awareness(ydoc), [ydoc]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!editKey) return;

    // Fetch doc metadata
    api.getDocByEditKey(editKey).then(doc => {
      setDocTitle(doc.title);
      setDocId(doc.id);
      setViewKey(doc.view_key);
    }).catch(() => {
      navigate('/dashboard', { replace: true });
    });

    // Connect WebSocket for document sync and control messages.
    const wsUrl = api.getWsUrl(editKey);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    let canPublish = false;

    const sendDocumentState = () => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({
        type: 'sync_update',
        data: encodeYjsUpdate(Y.encodeStateAsUpdate(ydoc)),
      }));
    };

    const sendAwarenessUpdate = (clientIds: number[]) => {
      if (ws.readyState !== WebSocket.OPEN || clientIds.length === 0) return;
      ws.send(JSON.stringify({
        type: 'awareness',
        data: encodeYjsUpdate(encodeAwarenessUpdate(awareness, clientIds)),
      }));
    };

    const handleYjsUpdate = (_update: Uint8Array, origin: unknown) => {
      if (origin === 'remote') return;
      if (!canPublish) return;
      sendDocumentState();
    };

    const handleAwarenessUpdate = (
      changes: { added: number[]; updated: number[]; removed: number[] },
      origin: unknown,
    ) => {
      if (origin === 'remote') return;
      sendAwarenessUpdate([...changes.added, ...changes.updated, ...changes.removed]);
    };

    const updateEditorCount = () => {
      setEditorCount(awareness.getStates().size);
    };

    ydoc.on('update', handleYjsUpdate);
    awareness.on('update', handleAwarenessUpdate);
    awareness.on('change', updateEditorCount);

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'approval_request') {
          setApprovalRequests(prev => [...prev, {
            user_id: msg.user_id,
            moniker: msg.moniker,
          }]);
        } else if (msg.type === 'connected') {
          setConnected(true);
          awareness.setLocalStateField('user', {
            name: msg.moniker,
            color: msg.color,
            colorLight: `${msg.color}40`,
          });
        } else if (msg.type === 'approved') {
          setConnected(true);
        } else if ((msg.type === 'sync_state' || msg.type === 'sync_update') && msg.data) {
          Y.applyUpdate(ydoc, decodeYjsUpdate(msg.data), 'remote');
        } else if (msg.type === 'awareness' && msg.data) {
          applyAwarenessUpdate(awareness, decodeYjsUpdate(msg.data), 'remote');
        } else if (msg.type === 'awareness_request') {
          sendAwarenessUpdate([awareness.clientID]);
        } else if (msg.type === 'sync_ready') {
          canPublish = true;
        }
      } catch { /* ignore malformed websocket messages */ }
    };

    return () => {
      awareness.setLocalState(null);
      awareness.off('change', updateEditorCount);
      awareness.off('update', handleAwarenessUpdate);
      ydoc.off('update', handleYjsUpdate);
      ws.close();
    };
  }, [awareness, editKey, navigate, ydoc]);

  useEffect(() => {
    return () => {
      awareness.destroy();
      ydoc.destroy();
    };
  }, [awareness, ydoc]);

  const editor = useEditor({
    extensions: [
      StarterKit, // History is not bundled in v3; Yjs handles undo/redo
      Placeholder.configure({
        placeholder: 'Start typing your document…',
      }),
      Highlight.configure({ multicolor: true }),
      Underline,
      TextStyle,
      Color,
      TextAlign.configure({
        types: ['heading', 'paragraph'],
      }),
      Collaboration.configure({ document: ydoc }),
      CollaborationCursor.configure({ awareness }),
    ],
    editorProps: {
      attributes: {
        class: 'tiptap',
      },
    },
  }, [awareness, ydoc]);

  const handleTitleChange = useCallback(async (newTitle: string) => {
    setDocTitle(newTitle);
    if (docId) {
      try {
        await api.updateTitle(docId, newTitle);
      } catch { /* silent fail */ }
    }
  }, [docId]);

  const handleApprove = useCallback((userId: string) => {
    wsRef.current?.send(JSON.stringify({
      type: 'approve_editor',
      user_id: userId,
    }));
    setApprovalRequests(prev => prev.filter(r => r.user_id !== userId));
  }, []);

  const handleDeny = useCallback((userId: string) => {
    wsRef.current?.send(JSON.stringify({
      type: 'deny_editor',
      user_id: userId,
    }));
    setApprovalRequests(prev => prev.filter(r => r.user_id !== userId));
  }, []);

  const copyLink = (type: 'edit' | 'view') => {
    const url = type === 'edit'
      ? `${window.location.origin}/edit/${editKey}`
      : `${window.location.origin}/view/${viewKey}`;
    navigator.clipboard.writeText(url);
    setCopied(type);
    setTimeout(() => setCopied(''), 2000);
  };

  return (
    <div className="editor-page">
      {/* Editor Header */}
      <header className="editor-header glass">
        <div className="editor-header-left">
          <button
            className="btn-icon"
            onClick={() => navigate('/dashboard')}
            title="Back to dashboard"
          >
            <ArrowLeft size={18} />
          </button>
          <input
            className="editor-title-input"
            value={docTitle}
            onChange={(e) => handleTitleChange(e.target.value)}
            placeholder="Untitled Document"
            id="doc-title-input"
          />
        </div>

        <div className="editor-header-right">
          <div className={`editor-status ${connected ? 'connected' : 'disconnected'}`}>
            {connected ? <Wifi size={14} /> : <WifiOff size={14} />}
            {connected ? 'Connected' : 'Offline'}
          </div>
          
          {/* 
          <div className="editor-users">
            <Users size={14} />
            <span>{editorCount}</span>
          </div> */}

          <div className="editor-share-wrapper">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setShowShare(!showShare)}
              id="share-btn"
            >
              <Share2 size={14} />
              Share
            </button>

            {showShare && (
              <div className="editor-share-dropdown glass animate-scale-in">
                <h4>Share this document</h4>
                <div className="share-link-row">
                  <div className="share-link-label">
                    <Pencil size={14} /> Edit Link
                  </div>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => copyLink('edit')}
                  >
                    {copied === 'edit' ? <Check size={14} /> : <Copy size={14} />}
                    {copied === 'edit' ? 'Copied!' : 'Copy'}
                  </button>
                </div>
                <div className="share-link-row">
                  <div className="share-link-label">
                    <Eye size={14} /> View Link
                  </div>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => copyLink('view')}
                  >
                    {copied === 'view' ? <Check size={14} /> : <Copy size={14} />}
                    {copied === 'view' ? 'Copied!' : 'Copy'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Toolbar */}
      {editor && <Toolbar editor={editor} />}

      {/* Editor Canvas */}
      <div className="editor-canvas">
        <div className="editor-page-container">
          <EditorContent editor={editor} />
        </div>
      </div>

      {/* Approval Popups */}
      {approvalRequests.map(req => (
        <ApprovalPopup
          key={req.user_id}
          moniker={req.moniker}
          onApprove={() => handleApprove(req.user_id)}
          onDeny={() => handleDeny(req.user_id)}
        />
      ))}
    </div>
  );
}

// Small inline icon (avoids another import in JSX)
function Pencil({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </svg>
  );
}

import { useState, useEffect, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Highlight from '@tiptap/extension-highlight';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import { TextStyle } from '@tiptap/extension-text-style';
import { Color } from '@tiptap/extension-color';
import Collaboration from '@tiptap/extension-collaboration';
import * as Y from 'yjs';
import { api } from '../lib/api';
import { decodeYjsUpdate } from '../lib/yjsSync';
import { Eye, FileText, Radio } from 'lucide-react';
import './Viewer.css';

export default function Viewer() {
  const { viewKey } = useParams<{ viewKey: string }>();
  const [docTitle, setDocTitle] = useState('Document');
  const [isLive, setIsLive] = useState(false);
  const ydoc = useMemo(() => new Y.Doc(), [viewKey]);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Highlight.configure({ multicolor: true }),
      Underline,
      TextStyle,
      Color,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      Collaboration.configure({ document: ydoc }),
    ],
    editable: false,
    editorProps: {
      attributes: { class: 'tiptap' },
    },
  }, [ydoc]);

  useEffect(() => {
    if (!viewKey) return;

    // Fetch doc metadata
    api.getDocByViewKey(viewKey)
      .then(doc => setDocTitle(doc.title))
      .catch(console.error);

    // SSE connection for live updates
    const eventSource = new EventSource(api.getViewerStreamUrl(viewKey));

    eventSource.onopen = () => setIsLive(true);
    eventSource.onerror = () => setIsLive(false);

    eventSource.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'state' && msg.data) {
          Y.applyUpdate(ydoc, decodeYjsUpdate(msg.data), 'remote');
        }
      } catch { /* ignore parse errors */ }
    };

    return () => {
      eventSource.close();
    };
  }, [viewKey, ydoc]);

  useEffect(() => {
    return () => {
      ydoc.destroy();
    };
  }, [ydoc]);

  return (
    <div className="viewer-page">
      <header className="viewer-header glass">
        <div className="viewer-header-left">
          <div className="viewer-logo-icon">
            <FileText size={18} />
          </div>
          <h1 className="viewer-title">{docTitle}</h1>
        </div>
        <div className="viewer-header-right">
          <div className={`viewer-badge ${isLive ? 'live' : ''}`}>
            {isLive ? <Radio size={14} /> : <Eye size={14} />}
            {isLive ? 'LIVE' : 'View Only'}
          </div>
        </div>
      </header>

      <div className="viewer-canvas">
        <div className="viewer-page-container">
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
}

import type { Editor } from '@tiptap/react';
import {
  Bold, Italic, Underline as UnderlineIcon, Strikethrough,
  Heading1, Heading2, Heading3, List, ListOrdered,
  AlignLeft, AlignCenter, AlignRight, Quote, Minus,
  Highlighter, Undo, Redo, Code,
} from 'lucide-react';
import './Toolbar.css';

interface ToolbarProps {
  editor: Editor;
}

export default function Toolbar({ editor }: ToolbarProps) {
  const tools = [
    {
      group: 'history',
      items: [
        { icon: <Undo size={16} />, action: () => editor.chain().focus().undo().run(), active: false, title: 'Undo' },
        { icon: <Redo size={16} />, action: () => editor.chain().focus().redo().run(), active: false, title: 'Redo' },
      ],
    },
    {
      group: 'heading',
      items: [
        { icon: <Heading1 size={16} />, action: () => editor.chain().focus().toggleHeading({ level: 1 }).run(), active: editor.isActive('heading', { level: 1 }), title: 'Heading 1' },
        { icon: <Heading2 size={16} />, action: () => editor.chain().focus().toggleHeading({ level: 2 }).run(), active: editor.isActive('heading', { level: 2 }), title: 'Heading 2' },
        { icon: <Heading3 size={16} />, action: () => editor.chain().focus().toggleHeading({ level: 3 }).run(), active: editor.isActive('heading', { level: 3 }), title: 'Heading 3' },
      ],
    },
    {
      group: 'format',
      items: [
        { icon: <Bold size={16} />, action: () => editor.chain().focus().toggleBold().run(), active: editor.isActive('bold'), title: 'Bold' },
        { icon: <Italic size={16} />, action: () => editor.chain().focus().toggleItalic().run(), active: editor.isActive('italic'), title: 'Italic' },
        { icon: <UnderlineIcon size={16} />, action: () => editor.chain().focus().toggleUnderline().run(), active: editor.isActive('underline'), title: 'Underline' },
        { icon: <Strikethrough size={16} />, action: () => editor.chain().focus().toggleStrike().run(), active: editor.isActive('strike'), title: 'Strikethrough' },
        { icon: <Code size={16} />, action: () => editor.chain().focus().toggleCode().run(), active: editor.isActive('code'), title: 'Code' },
        { icon: <Highlighter size={16} />, action: () => editor.chain().focus().toggleHighlight().run(), active: editor.isActive('highlight'), title: 'Highlight' },
      ],
    },
    {
      group: 'align',
      items: [
        { icon: <AlignLeft size={16} />, action: () => editor.chain().focus().setTextAlign('left').run(), active: editor.isActive({ textAlign: 'left' }), title: 'Align Left' },
        { icon: <AlignCenter size={16} />, action: () => editor.chain().focus().setTextAlign('center').run(), active: editor.isActive({ textAlign: 'center' }), title: 'Align Center' },
        { icon: <AlignRight size={16} />, action: () => editor.chain().focus().setTextAlign('right').run(), active: editor.isActive({ textAlign: 'right' }), title: 'Align Right' },
      ],
    },
    {
      group: 'list',
      items: [
        { icon: <List size={16} />, action: () => editor.chain().focus().toggleBulletList().run(), active: editor.isActive('bulletList'), title: 'Bullet List' },
        { icon: <ListOrdered size={16} />, action: () => editor.chain().focus().toggleOrderedList().run(), active: editor.isActive('orderedList'), title: 'Ordered List' },
        { icon: <Quote size={16} />, action: () => editor.chain().focus().toggleBlockquote().run(), active: editor.isActive('blockquote'), title: 'Blockquote' },
        { icon: <Minus size={16} />, action: () => editor.chain().focus().setHorizontalRule().run(), active: false, title: 'Horizontal Rule' },
      ],
    },
  ];

  return (
    <div className="toolbar glass">
      {tools.map((group) => (
        <div key={group.group} className="toolbar-group">
          {group.items.map((tool, i) => (
            <button
              key={i}
              className={`btn-icon ${tool.active ? 'active' : ''}`}
              onClick={tool.action}
              title={tool.title}
              id={`tool-${tool.title.toLowerCase().replace(/\s/g, '-')}`}
            >
              {tool.icon}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

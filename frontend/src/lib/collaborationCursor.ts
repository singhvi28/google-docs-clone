import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { EditorState, Transaction } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import type { Awareness } from 'y-protocols/awareness';

type AwarenessEvents = Awareness & {
  on: (name: 'change', handler: () => void) => void;
  off: (name: 'change', handler: () => void) => void;
};

type CursorUser = {
  name?: string;
  color?: string;
  colorLight?: string;
};

type CursorPosition = {
  anchor: number;
  head: number;
};

type CursorAwarenessState = {
  user?: CursorUser;
  cursor?: CursorPosition | null;
};

type CollaborationCursorOptions = {
  awareness: Awareness;
};

const cursorPluginKey = new PluginKey<DecorationSet>('collaborationCursor');

function clampPosition(position: number, state: EditorState): number {
  return Math.max(0, Math.min(position, state.doc.content.size));
}

function createCursorWidget(user: CursorUser): HTMLElement {
  const caret = document.createElement('span');
  const color = user.color || '#6c5ce7';

  caret.className = 'collaboration-cursor__caret';
  caret.style.borderColor = color;

  const label = document.createElement('span');
  label.className = 'collaboration-cursor__label';
  label.style.backgroundColor = color;
  label.textContent = user.name || 'Guest';

  caret.appendChild(label);
  return caret;
}

function buildDecorations(state: EditorState, awareness: Awareness): DecorationSet {
  const decorations: Decoration[] = [];

  awareness.getStates().forEach((awarenessState, clientId) => {
    if (clientId === awareness.clientID) return;

    const { cursor, user } = awarenessState as CursorAwarenessState;
    if (!cursor || !user) return;

    const anchor = clampPosition(cursor.anchor, state);
    const head = clampPosition(cursor.head, state);
    const from = Math.min(anchor, head);
    const to = Math.max(anchor, head);

    if (from !== to) {
      decorations.push(Decoration.inline(from, to, {
        style: `background-color: ${user.colorLight || `${user.color || '#6c5ce7'}40`}`,
      }));
    }

    decorations.push(Decoration.widget(head, () => createCursorWidget(user), {
      key: `collaboration-cursor-${clientId}`,
      side: -1,
    }));
  });

  return DecorationSet.create(state.doc, decorations);
}

export const CollaborationCursor = Extension.create<CollaborationCursorOptions>({
  name: 'collaborationCursor',

  addOptions() {
    return {
      awareness: null as unknown as Awareness,
    };
  },

  addProseMirrorPlugins() {
    const { awareness } = this.options;

    return [
      new Plugin<DecorationSet>({
        key: cursorPluginKey,
        state: {
          init: (_config, state) => buildDecorations(state, awareness),
          apply: (transaction: Transaction, oldDecorations, _oldState, newState) => {
            if (transaction.docChanged || transaction.getMeta(cursorPluginKey)) {
              return buildDecorations(newState, awareness);
            }

            return oldDecorations;
          },
        },
        props: {
          decorations: state => cursorPluginKey.getState(state) || DecorationSet.empty,
        },
        view: (view: EditorView) => {
          const redraw = () => {
            view.dispatch(view.state.tr.setMeta(cursorPluginKey, true));
          };

          const updateLocalCursor = (currentView: EditorView) => {
            const { anchor, head } = currentView.state.selection;
            awareness.setLocalStateField('cursor', { anchor, head });
          };

          (awareness as AwarenessEvents).on('change', redraw);
          updateLocalCursor(view);

          return {
            update: (currentView, previousState) => {
              const selectionChanged = !currentView.state.selection.eq(previousState.selection);
              if (selectionChanged || currentView.state.doc !== previousState.doc) {
                updateLocalCursor(currentView);
              }
            },
            destroy: () => {
              (awareness as AwarenessEvents).off('change', redraw);
              awareness.setLocalStateField('cursor', null);
            },
          };
        },
      }),
    ];
  },
});

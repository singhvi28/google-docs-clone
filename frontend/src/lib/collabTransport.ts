// Collab transport: prefer WebTransport (QUIC) for editors, fall back to WebSocket.

// - Datagrams carry awareness / cursor updates (unreliable, ultra-fast)
// - Bidirectional streams carry Yjs sync deltas (reliable, ordered)


export type CollabMessage = {
  type: string;
  data?: string;
  moniker?: string;
  color?: string;
  user_id?: string;
  message?: string;
  [key: string]: unknown;
};

export type CollabTransport = {
  readonly kind: 'webtransport' | 'websocket';
  sendSync(data: string): void;
  sendAwareness(data: string): void;
  sendJson(msg: CollabMessage): void;
  close(): void;
};

type Handlers = {
  onOpen: () => void;
  onClose: () => void;
  onMessage: (msg: CollabMessage) => void;
};

function parseJson(text: string): CollabMessage | null {
  try {
    return JSON.parse(text) as CollabMessage;
  } catch {
    return null;
  }
}

class WebSocketTransport implements CollabTransport {
  readonly kind = 'websocket' as const;
  private ws: WebSocket;

  constructor(url: string, handlers: Handlers) {
    this.ws = new WebSocket(url);
    this.ws.onopen = () => handlers.onOpen();
    this.ws.onclose = () => handlers.onClose();
    this.ws.onerror = () => handlers.onClose();
    this.ws.onmessage = (event) => {
      const msg = parseJson(String(event.data));
      if (msg) handlers.onMessage(msg);
    };
  }

  sendSync(data: string) {
    this.sendJson({ type: 'sync_update', data });
  }

  sendAwareness(data: string) {
    this.sendJson({ type: 'awareness', data });
  }

  sendJson(msg: CollabMessage) {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
  }

  close() {
    this.ws.close();
  }
}

class WebTransportCollab implements CollabTransport {
  readonly kind = 'webtransport' as const;
  private transport: WebTransport;
  private streamWriter: WritableStreamDefaultWriter<Uint8Array> | null = null;
  private datagramWriter: WritableStreamDefaultWriter<Uint8Array> | null = null;
  private encoder = new TextEncoder();
  private decoder = new TextDecoder();
  private closed = false;

  private constructor(transport: WebTransport) {
    this.transport = transport;
  }

  static async connect(url: string, handlers: Handlers): Promise<WebTransportCollab> {
    const transport = new WebTransport(url);
    await transport.ready;
    const collab = new WebTransportCollab(transport);

    const stream = await transport.createBidirectionalStream();
    collab.streamWriter = stream.writable.getWriter();
    collab.datagramWriter = transport.datagrams.writable.getWriter();

    // Read reliable sync / control messages from the client→server stream echo
    // and from server-initiated streams via incomingBidirectionalStreams.
    void collab._readStream(stream.readable, handlers);
    void collab._acceptIncomingStreams(handlers);
    void collab._readDatagrams(handlers);

    transport.closed.then(() => {
      if (!collab.closed) handlers.onClose();
    }).catch(() => {
      if (!collab.closed) handlers.onClose();
    });

    handlers.onOpen();
    return collab;
  }

  private async _acceptIncomingStreams(handlers: Handlers) {
    const reader = this.transport.incomingBidirectionalStreams.getReader();
    try {
      while (true) {
        const { value: stream, done } = await reader.read();
        if (done || !stream) break;
        void this._readStream(stream.readable, handlers);
      }
    } catch {
      /* transport closed */
    }
  }

  private async _readStream(
    readable: ReadableStream<Uint8Array>,
    handlers: Handlers,
  ) {
    const reader = readable.getReader();
    let buffer = '';
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += this.decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 1);
          const msg = parseJson(line);
          if (msg) handlers.onMessage(msg);
        }
      }
    } catch {
      /* stream closed */
    }
  }

  private async _readDatagrams(handlers: Handlers) {
    const reader = this.transport.datagrams.readable.getReader();
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done || !value) break;
        const msg = parseJson(this.decoder.decode(value));
        if (msg) handlers.onMessage(msg);
      }
    } catch {
      /* transport closed */
    }
  }

  sendSync(data: string) {
    if (!this.streamWriter) return;
    const frame = this.encoder.encode(JSON.stringify({ type: 'sync_update', data }) + '\n');
    void this.streamWriter.write(frame);
  }

  sendAwareness(data: string) {
    if (!this.datagramWriter) return;
    const payload = this.encoder.encode(JSON.stringify({ type: 'awareness', data }));
    void this.datagramWriter.write(payload);
  }

  sendJson(msg: CollabMessage) {
    // Control messages (approve/deny) go on the reliable stream
    if (!this.streamWriter) return;
    const frame = this.encoder.encode(JSON.stringify(msg) + '\n');
    void this.streamWriter.write(frame);
  }

  close() {
    this.closed = true;
    try {
      this.transport.close();
    } catch {
      /* already closed */
    }
  }
}

/**
 * Prefer WebTransport when the browser supports it and a WT URL is configured;
 * otherwise fall back to classic WebSockets over TCP.
 */
export async function connectCollabTransport(
  wsUrl: string,
  wtUrl: string | null,
  handlers: Handlers,
): Promise<CollabTransport> {
  if (wtUrl && typeof WebTransport !== 'undefined') {
    try {
      return await WebTransportCollab.connect(wtUrl, handlers);
    } catch {
      // TLS / QUIC unavailable in this environment — fall back
    }
  }
  return new WebSocketTransport(wsUrl, handlers);
}

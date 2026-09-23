/**
 * WebSocket client with automatic reconnection.
 *
 * All WebSocket connections in the app must go through this module.
 * Handles exponential backoff reconnect and exposes a clean
 * subscribe/unsubscribe interface for message listeners.
 */

type MessageListener<T> = (data: T) => void;
type StatusListener = (connected: boolean) => void;

const INITIAL_DELAY_MS = 500;
const MAX_DELAY_MS = 30_000;
const BACKOFF_FACTOR = 2;
// Close codes sent by the backend for an invalid token (4001) or a machine the user may not watch (4003).
const NO_RETRY_CODES = new Set([4001, 4003]);

export class WsClient<T = unknown> {
  private ws: WebSocket | null = null;
  private messageListeners: Set<MessageListener<T>> = new Set();
  private statusListeners: Set<StatusListener> = new Set();
  private delay = INITIAL_DELAY_MS;
  private closed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly url: string) {}

  connect(): void {
    if (this.closed) return;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.delay = INITIAL_DELAY_MS;
      this.notifyStatus(true);
    };

    this.ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as T;
        this.messageListeners.forEach((l) => l(data));
      } catch {
        // Ignore malformed frames.
      }
    };

    this.ws.onclose = (event: CloseEvent) => {
      this.notifyStatus(false);
      // The server refused the token or the machine: retrying cannot help.
      if (NO_RETRY_CODES.has(event.code)) {
        this.closed = true;
        return;
      }
      if (!this.closed) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect(): void {
    this.reconnectTimer = setTimeout(() => {
      this.delay = Math.min(this.delay * BACKOFF_FACTOR, MAX_DELAY_MS);
      this.connect();
    }, this.delay);
  }

  private notifyStatus(connected: boolean): void {
    this.statusListeners.forEach((l) => l(connected));
  }

  send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  onMessage(listener: MessageListener<T>): () => void {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  disconnect(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
    }
    this.ws?.close();
  }
}

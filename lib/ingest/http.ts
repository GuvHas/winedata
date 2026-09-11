/**
 * Small HTTP client for the ingestion scripts.
 *
 * Responsibilities: a browser-ish User-Agent, optional session cookies, polite
 * spacing between requests, bounded retries with exponential backoff, and
 * timeouts so a hung connection cannot stall a sync.
 */

export interface HttpOptions {
  /** Cookie header forwarded with every request (for member-only pages). */
  cookie?: string | null;
  /** Minimum milliseconds between two requests to the same client. */
  delayMs?: number;
  /** Attempts per URL, including the first. */
  retries?: number;
  /** Per-request timeout in milliseconds. */
  timeoutMs?: number;
  /** Called with progress/diagnostic messages. */
  onLog?: (message: string) => void;
}

const DEFAULT_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly url: string,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class HttpClient {
  private readonly cookie: string | null;
  private readonly delayMs: number;
  private readonly retries: number;
  private readonly timeoutMs: number;
  private readonly onLog: (message: string) => void;
  /** Timestamp of the last request, used to space out calls. */
  private lastRequestAt = 0;

  constructor(options: HttpOptions = {}) {
    this.cookie = options.cookie?.trim() ? options.cookie.trim() : null;
    this.delayMs = options.delayMs ?? 750;
    this.retries = Math.max(1, options.retries ?? 3);
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.onLog = options.onLog ?? (() => {});
  }

  /** True when a session cookie was supplied. */
  get authenticated(): boolean {
    return this.cookie !== null;
  }

  /** Fetch a URL as text, retrying transient failures. */
  async getText(url: string): Promise<string> {
    const response = await this.get(url);
    return response.text();
  }

  /** Fetch a URL as JSON, retrying transient failures. */
  async getJson<T>(url: string, headers: Record<string, string> = {}): Promise<T> {
    const response = await this.get(url, { Accept: 'application/json', ...headers });
    return (await response.json()) as T;
  }

  private async get(url: string, extraHeaders: Record<string, string> = {}): Promise<Response> {
    let lastError: unknown;

    for (let attempt = 1; attempt <= this.retries; attempt += 1) {
      await this.throttle();

      try {
        return await this.attempt(url, extraHeaders);
      } catch (error) {
        lastError = error;

        // A 4xx other than 429 will not succeed on retry — fail immediately.
        if (error instanceof HttpError && error.status !== 429 && error.status < 500) {
          throw error;
        }
        if (attempt === this.retries) break;

        const backoff = this.delayMs * 2 ** attempt;
        this.onLog(
          `  retry ${attempt}/${this.retries - 1} in ${backoff}ms — ${describe(error)}`,
        );
        await sleep(backoff);
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new Error(`Failed to fetch ${url}: ${String(lastError)}`);
  }

  private async attempt(url: string, extraHeaders: Record<string, string>): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url, {
        signal: controller.signal,
        redirect: 'follow',
        headers: {
          'User-Agent': DEFAULT_USER_AGENT,
          'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.8',
          Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          ...(this.cookie ? { Cookie: this.cookie } : {}),
          ...extraHeaders,
        },
      });

      if (!response.ok) {
        throw new HttpError(`HTTP ${response.status} for ${url}`, response.status, url);
      }
      return response;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Keep at least `delayMs` between consecutive requests. */
  private async throttle(): Promise<void> {
    const elapsed = Date.now() - this.lastRequestAt;
    if (this.lastRequestAt > 0 && elapsed < this.delayMs) {
      await sleep(this.delayMs - elapsed);
    }
    this.lastRequestAt = Date.now();
  }
}

function describe(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

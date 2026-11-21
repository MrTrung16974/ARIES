import {
  Component,
  ElementRef,
  inject,
  input,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatIcon } from '@angular/material/icon';
import { APP_CONFIG, Citation, parseSseChunk } from '@aiagent/core';

interface PublicInfo {
  name: string;
  description: string;
  opening_statement: string;
  starter_prompts: string[];
}

interface Bubble {
  role: 'user' | 'assistant';
  text: string;
  messageId?: number;
  citations?: Citation[];
  rating?: 1 | -1;
  busy?: boolean;
  error?: string;
}

/** Sinh mã khách ngẫu nhiên (không định danh người dùng) lưu trong trình duyệt. */
function visitorId(): string {
  const key = 'aries_visitor';
  try {
    const existing = localStorage.getItem(key);
    if (existing && /^[A-Za-z0-9-]{16,64}$/.test(existing)) return existing;
    const fresh = crypto.randomUUID();
    localStorage.setItem(key, fresh);
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

/**
 * Trang chat công khai của link chia sẻ `/c/:token` (ADR-0020): không cần đăng nhập, dùng được
 * độc lập hoặc nhúng trong iframe (widget). Không gửi access token; mọi quyền do server quyết.
 */
@Component({
  selector: 'app-share-chat',
  imports: [ReactiveFormsModule, MatIcon],
  host: { '[class.embed]': 'embedded' },
  template: `
    <div class="public-chat">
      <header>
        <img src="assets/brand/aries-mark-64.png" alt="" width="28" height="28" />
        <div>
          <strong>{{ info()?.name ?? 'Trợ lý' }}</strong>
          @if (info()?.description) {
            <div class="muted">{{ info()!.description }}</div>
          }
        </div>
        @if (thread()) {
          <button type="button" class="ghost" (click)="reset()" title="Hội thoại mới">Mới</button>
        }
      </header>

      @if (fatal()) {
        <div class="fatal">{{ fatal() }}</div>
      } @else {
        <main #scroller>
          @if (!bubbles().length) {
            <div class="welcome">
              <p>{{ info()?.opening_statement || 'Xin chào! Mình có thể giúp gì cho bạn?' }}</p>
              <div class="starters">
                @for (s of info()?.starter_prompts ?? []; track s) {
                  <button type="button" (click)="ask(s)">{{ s }}</button>
                }
              </div>
            </div>
          }
          @for (b of bubbles(); track $index) {
            <div class="msg" [class.user]="b.role === 'user'">
              <div class="bubble">{{ b.text }}@if (b.busy && !b.text) {<span class="dots">…</span>}</div>
              @if (b.citations?.length) {
                <div class="sources">
                  @for (c of b.citations; track c.n) {
                    <span class="source" [title]="c.filename">[{{ c.n }}] {{ c.title }}@if (c.page) {, tr. {{ c.page }}}</span>
                  }
                </div>
              }
              @if (b.error) {
                <div class="err">{{ b.error }}</div>
              }
              @if (b.role === 'assistant' && b.messageId && !b.busy) {
                <div class="rate">
                  <button type="button" [class.on]="b.rating === 1" (click)="rate(b, 1)" aria-label="Hữu ích">
                    <mat-icon svgIcon="thumb_up" />
                  </button>
                  <button type="button" [class.on]="b.rating === -1" (click)="rate(b, -1)" aria-label="Chưa tốt">
                    <mat-icon svgIcon="thumb_down" />
                  </button>
                </div>
              }
            </div>
          }
        </main>
        <form (submit)="$event.preventDefault(); send()">
          <textarea
            rows="1"
            [formControl]="input"
            (keydown)="onKey($event)"
            placeholder="Nhập câu hỏi…"
            maxlength="4000"
          ></textarea>
          <button type="submit" [disabled]="busy() || !input.value.trim()" aria-label="Gửi">
            <mat-icon svgIcon="send" />
          </button>
        </form>
        <footer>Trả lời bởi AI — có thể chưa chính xác. Không nhập thông tin cá nhân nhạy cảm.</footer>
      }
    </div>
  `,
  styles: `
    :host { display: block; height: 100vh; background: #f7f5fb; color: #1d1b26;
      font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
    .public-chat { display: flex; flex-direction: column; height: 100%; max-width: 860px; margin: 0 auto;
      background: #fff; box-shadow: 0 0 0 1px #ece8f3; }
    :host(.embed) .public-chat { max-width: none; box-shadow: none; }
    header { display: flex; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem;
      border-bottom: 1px solid #ece8f3; }
    header img { border-radius: 50%; }
    header > div { flex: 1; min-width: 0; }
    .muted { color: #6b6680; font-size: 0.8rem; }
    .ghost { border: 1px solid #ddd6ea; background: none; border-radius: 999px; padding: 0.25rem 0.7rem;
      cursor: pointer; font: inherit; font-size: 0.8rem; }
    main { flex: 1; overflow-y: auto; padding: 1rem; display: flex; flex-direction: column; gap: 0.7rem; }
    .welcome p { margin: 0 0 0.8rem; }
    .starters { display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .starters button { border: 1px solid #e4d9f2; background: #faf7fe; border-radius: 999px; cursor: pointer;
      padding: 0.35rem 0.8rem; font: inherit; font-size: 0.85rem; }
    .msg { display: flex; flex-direction: column; align-items: flex-start; max-width: 85%; }
    .msg.user { align-self: flex-end; align-items: flex-end; }
    .bubble { white-space: pre-wrap; padding: 0.55rem 0.85rem; border-radius: 14px; background: #f1edf7;
      line-height: 1.45; word-break: break-word; }
    .msg.user .bubble { background: #e11d74; color: #fff; }
    .sources { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.3rem; }
    .source { font-size: 0.72rem; border: 1px solid #e4d9f2; border-radius: 999px; padding: 0.1rem 0.5rem;
      max-width: 24rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .rate { display: flex; gap: 0.2rem; margin-top: 0.2rem; }
    .rate button { border: none; background: none; cursor: pointer; opacity: 0.45; padding: 0.1rem; }
    .rate button.on, .rate button:hover { opacity: 1; }
    .rate mat-icon { width: 16px; height: 16px; }
    .err, .fatal { color: #b91c1c; font-size: 0.85rem; margin-top: 0.25rem; }
    .fatal { padding: 2rem; text-align: center; }
    form { display: flex; gap: 0.5rem; padding: 0.6rem 0.8rem; border-top: 1px solid #ece8f3; }
    textarea { flex: 1; resize: none; border: 1px solid #ddd6ea; border-radius: 12px; padding: 0.55rem 0.7rem;
      font: inherit; max-height: 8rem; }
    form button { border: none; background: #e11d74; color: #fff; border-radius: 12px; width: 44px;
      cursor: pointer; }
    form button:disabled { opacity: 0.4; cursor: default; }
    footer { font-size: 0.7rem; color: #8a849c; text-align: center; padding: 0 0.5rem 0.5rem; }
  `,
})
export class ShareChat implements OnInit {
  readonly token = input.required<string>();
  readonly embed = input<string>();
  readonly origin = input<string>();

  private readonly base = inject(APP_CONFIG).apiBaseUrl;
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected readonly info = signal<PublicInfo | null>(null);
  protected readonly bubbles = signal<Bubble[]>([]);
  protected readonly thread = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly fatal = signal<string | null>(null);
  protected readonly input = new FormControl('', { nonNullable: true });
  protected embedded = false;

  private readonly visitor = visitorId();

  private get threadKey(): string {
    return `aries_thread_${this.token().slice(-12)}`;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Visitor-Id': this.visitor,
    };
    const embedOrigin = this.embedOrigin();
    if (embedOrigin) h['X-Embed-Origin'] = embedOrigin;
    return h;
  }

  private embedOrigin(): string | null {
    if (!this.embedded) return null;
    try {
      if (document.referrer) return new URL(document.referrer).origin;
    } catch {
      // bỏ qua referrer hỏng
    }
    return this.origin() ?? null;
  }

  private url(path = ''): string {
    return `${this.base}/v1/public/share/${encodeURIComponent(this.token())}${path}`;
  }

  async ngOnInit(): Promise<void> {
    this.embedded = this.embed() === '1' || window.self !== window.top;
    const r = await fetch(this.url(), { headers: this.headers(), credentials: 'omit' });
    if (!r.ok) {
      this.fatal.set('Link không tồn tại hoặc đã bị tắt.');
      return;
    }
    this.info.set((await r.json()) as PublicInfo);
    const saved = this.readThread();
    if (saved) await this.restore(saved);
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void this.send();
    }
  }

  protected ask(text: string): void {
    this.input.setValue(text);
    void this.send();
  }

  protected reset(): void {
    this.thread.set(null);
    this.bubbles.set([]);
    try {
      localStorage.removeItem(this.threadKey);
    } catch {
      // ignore
    }
  }

  protected async send(): Promise<void> {
    const message = this.input.value.trim();
    if (!message || this.busy()) return;
    this.input.setValue('');
    this.busy.set(true);
    const reply: Bubble = { role: 'assistant', text: '', busy: true, citations: [] };
    this.bubbles.update((l) => [...l, { role: 'user', text: message }, reply]);
    this.scroll();
    const touch = () => this.bubbles.update((l) => [...l]);
    try {
      const r = await fetch(this.url('/chat'), {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({ message, thread_id: this.thread() }),
        credentials: 'omit',
      });
      if (!r.ok || !r.body) {
        const body = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
        reply.error = body.error?.message ?? `Lỗi ${r.status}`;
        if (r.status === 404 || r.status === 409) this.reset();
        return;
      }
      const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const parsed = parseSseChunk(buffer);
        buffer = parsed.rest;
        for (const ev of parsed.events) {
          const d = ev.data;
          if (ev.event === 'run_started' && d['thread_id']) this.saveThread(String(d['thread_id']));
          else if (ev.event === 'token') reply.text += String(d['text'] ?? '');
          else if (ev.event === 'tool_start') reply.text = '';
          else if (ev.event === 'tool_end' && Array.isArray(d['citations']))
            reply.citations = d['citations'] as Citation[];
          else if (ev.event === 'error') reply.error = String(d['message'] ?? 'Lỗi');
          else if (ev.event === 'done' && d['message_id']) reply.messageId = Number(d['message_id']);
          touch();
          this.scroll();
        }
      }
    } catch {
      reply.error = 'Mất kết nối — hãy thử lại.';
    } finally {
      reply.busy = false;
      touch();
      this.busy.set(false);
    }
  }

  protected async rate(b: Bubble, rating: 1 | -1): Promise<void> {
    const thread = this.thread();
    if (!thread || !b.messageId) return;
    const r = await fetch(this.url('/feedback'), {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ thread_id: thread, message_id: b.messageId, rating }),
      credentials: 'omit',
    });
    if (r.ok) {
      b.rating = rating;
      this.bubbles.update((l) => [...l]);
    }
  }

  private async restore(threadId: string): Promise<void> {
    const r = await fetch(this.url(`/threads/${encodeURIComponent(threadId)}/messages`), {
      headers: this.headers(),
      credentials: 'omit',
    });
    if (!r.ok) {
      this.reset();
      return;
    }
    const msgs = (await r.json()) as { id: number; role: 'user' | 'assistant'; text: string }[];
    this.thread.set(threadId);
    this.bubbles.set(
      msgs.map((m) => ({ role: m.role, text: m.text, messageId: m.role === 'assistant' ? m.id : undefined })),
    );
    this.scroll();
  }

  private readThread(): string | null {
    try {
      return localStorage.getItem(this.threadKey);
    } catch {
      return null;
    }
  }

  private saveThread(id: string): void {
    this.thread.set(id);
    try {
      localStorage.setItem(this.threadKey, id);
    } catch {
      // ignore
    }
  }

  private scroll(): void {
    queueMicrotask(() => {
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}

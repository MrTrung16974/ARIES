import { DecimalPipe } from '@angular/common';
import {
  Component,
  computed,
  ElementRef,
  inject,
  input,
  OnDestroy,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  AgentDetail,
  AgentsApi,
  ChatStreamError,
  ChatStreamService,
  StoredMessage,
  ThreadInfo,
} from '@aiagent/core';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';

interface ToolStep {
  id: string;
  name: string;
  status: 'running' | 'ok' | 'error';
  preview?: string;
  durationMs?: number;
}

interface ChatItem {
  role: 'user' | 'assistant';
  text: string;
  tools: ToolStep[];
  thinking?: string;
  error?: string;
  streaming?: boolean;
}

interface RunUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cost_usd: number;
}

/** Dựng lại hội thoại từ content block đã lưu (chỉ text và tool; bỏ thinking). */
export function toChatItems(messages: StoredMessage[]): ChatItem[] {
  const items: ChatItem[] = [];
  for (const m of messages) {
    if (m.role === 'user') {
      const results = m.content.filter((b) => b.type === 'tool_result');
      if (results.length) {
        const last = items.at(-1);
        for (const r of results) {
          const step = last?.tools.find((t) => t.id === r['tool_use_id']);
          if (step) {
            step.status = r['is_error'] ? 'error' : 'ok';
            step.preview = String(r['content'] ?? '').slice(0, 500);
          }
        }
        continue;
      }
      const text = m.content
        .filter((b) => b.type === 'text')
        .map((b) => String(b['text']))
        .join('');
      items.push({ role: 'user', text, tools: [] });
      continue;
    }
    let item = items.at(-1);
    if (!item || item.role !== 'assistant') {
      item = { role: 'assistant', text: '', tools: [] };
      items.push(item);
    }
    for (const b of m.content) {
      if (b.type === 'text') item.text += String(b['text']);
      if (b.type === 'tool_use') {
        item.tools.push({
          id: String(b['id']),
          name: String(b['name']).replace(/__/g, '.'),
          status: 'running',
        });
      }
    }
  }
  return items;
}

@Component({
  selector: 'app-agent-chat',
  imports: [
    FormsModule,
    RouterLink,
    ButtonModule,
    MessageModule,
    TagModule,
    TextareaModule,
    DecimalPipe,
  ],
  templateUrl: './agent-chat.html',
  styleUrl: './agent-chat.scss',
})
export class AgentChat implements OnInit, OnDestroy {
  readonly id = input.required<string>();
  readonly draft = input<string>();

  private readonly api = inject(AgentsApi);
  private readonly stream = inject(ChatStreamService);
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly threads = signal<ThreadInfo[]>([]);
  protected readonly threadId = signal<string | null>(null);
  protected readonly items = signal<ChatItem[]>([]);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly usage = signal<RunUsage | null>(null);
  protected input = '';

  protected readonly isDraft = computed(
    () => !!this.draft() && !!this.agent()?.can_edit,
  );
  protected readonly versionLabel = computed(() => {
    const a = this.agent();
    if (!a) return '';
    return this.isDraft()
      ? `Bản nháp v${a.latest_version}`
      : `v${a.published_version ?? '?'}`;
  });

  private abort: AbortController | null = null;

  ngOnInit(): void {
    this.api.get(this.id()).subscribe({
      next: (a) => this.agent.set(a),
      error: () => this.error.set('Không tải được agent.'),
    });
    this.loadThreads();
  }

  ngOnDestroy(): void {
    this.abort?.abort();
  }

  protected newThread(): void {
    this.threadId.set(null);
    this.items.set([]);
    this.usage.set(null);
    this.error.set(null);
  }

  protected openThread(t: ThreadInfo): void {
    if (this.busy()) return;
    this.threadId.set(t.id);
    this.error.set(null);
    this.usage.set(null);
    this.api.messages(t.id).subscribe((m) => {
      this.items.set(toChatItems(m));
      this.scrollDown();
    });
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void this.send();
    }
  }

  protected useStarter(text: string): void {
    this.input = text;
    void this.send();
  }

  protected stop(): void {
    this.abort?.abort();
  }

  protected async send(): Promise<void> {
    const message = this.input.trim();
    if (!message || this.busy()) return;
    this.input = '';
    this.error.set(null);
    this.busy.set(true);

    const reply: ChatItem = {
      role: 'assistant',
      text: '',
      tools: [],
      streaming: true,
    };
    this.items.update((list) => [
      ...list,
      { role: 'user', text: message, tools: [] },
      reply,
    ]);
    this.scrollDown();
    const touch = (): void => this.items.update((list) => [...list]);

    this.abort = new AbortController();
    try {
      for await (const ev of this.stream.chat(
        {
          agentId: this.id(),
          message,
          threadId: this.threadId(),
          draft: this.isDraft(),
        },
        this.abort.signal,
      )) {
        const d = ev.data;
        switch (ev.event) {
          case 'run_started':
            this.threadId.set(String(d['thread_id']));
            break;
          case 'token':
            reply.text += String(d['text'] ?? '');
            reply.thinking = undefined;
            break;
          case 'thinking':
            reply.thinking = String(d['text'] ?? '');
            break;
          case 'tool_start':
            reply.tools.push({
              id: String(d['id']),
              name: String(d['name']),
              status: 'running',
            });
            break;
          case 'tool_end': {
            const step = reply.tools.find((t) => t.id === d['id']);
            if (step) {
              step.status = d['is_error'] ? 'error' : 'ok';
              step.name = String(d['name'] ?? step.name);
              step.preview = d['preview'] as string | undefined;
              step.durationMs = d['duration_ms'] as number | undefined;
            }
            break;
          }
          case 'usage':
            if (!d['partial']) this.usage.set(d as unknown as RunUsage);
            break;
          case 'error':
            reply.error = String(d['message'] ?? 'Lỗi');
            break;
        }
        touch();
        this.scrollDown();
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        reply.error = 'Đã dừng.';
      } else if (e instanceof ChatStreamError) {
        reply.error = e.message;
        if (e.status === 409) this.threadId.set(null);
      } else {
        reply.error = 'Mất kết nối tới máy chủ.';
      }
    } finally {
      reply.streaming = false;
      reply.thinking = undefined;
      touch();
      this.busy.set(false);
      this.abort = null;
      this.loadThreads();
    }
  }

  private loadThreads(): void {
    this.api
      .threads(this.id())
      .subscribe((t) =>
        this.threads.set(t.filter((x) => x.is_draft === this.isDraft())),
      );
  }

  private scrollDown(): void {
    queueMicrotask(() => {
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}

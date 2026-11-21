import { DecimalPipe, JsonPipe } from '@angular/common';
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
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  AgentDetail,
  AgentsApi,
  Approval,
  ApprovalRequest,
  ApprovalsApi,
  ChannelsApi,
  ChatStreamError,
  ChatStreamService,
  Citation,
  Role,
  RunEventMessage,
  SessionService,
  StoredMessage,
  ThreadInfo,
} from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Notice, Tag } from '@aiagent/ui';

interface ToolStep {
  id: string;
  name: string;
  status: 'running' | 'ok' | 'error';
  preview?: string;
  durationMs?: number;
  citations?: Citation[];
}

export interface ApprovalCard {
  id: string;
  toolUseId: string;
  tool: string;
  risk: string;
  reason: string;
  arguments: Record<string, unknown>;
  approverRoles: string[];
  status: 'pending' | 'approved' | 'denied' | 'expired';
  agentName?: string;
  canDecide: boolean;
  note?: string | null;
  deciding?: boolean;
}

export interface DelegationView {
  id: string;
  toolUseId: string;
  agentName: string;
  task: string;
  status: 'running' | 'completed' | 'failed' | 'awaiting_approval';
  steps: ToolStep[];
  summary?: string;
}

interface ChatItem {
  role: 'user' | 'assistant';
  text: string;
  tools: ToolStep[];
  delegations?: DelegationView[];
  approvals?: ApprovalCard[];
  canResume?: boolean;
  messageId?: number;
  rating?: 1 | -1;
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
    ReactiveFormsModule,
    RouterLink,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatProgressSpinnerModule,
    Notice,
    Tag,
    DecimalPipe,
    JsonPipe,
  ],
  templateUrl: './agent-chat.html',
  styleUrl: './agent-chat.scss',
})
export class AgentChat implements OnInit, OnDestroy {
  readonly id = input.required<string>();
  readonly draft = input<string>();
  readonly q = input<string>();
  /** `?thread=<id>`: mở thẳng một hội thoại (ví dụ lần chạy của lịch tự động). */
  readonly thread = input<string>();

  private readonly api = inject(AgentsApi);
  private readonly stream = inject(ChatStreamService);
  private readonly approvalsApi = inject(ApprovalsApi);
  private readonly channelsApi = inject(ChannelsApi);
  private readonly session = inject(SessionService);
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly threads = signal<ThreadInfo[]>([]);
  protected readonly threadId = signal<string | null>(null);
  protected readonly items = signal<ChatItem[]>([]);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly usage = signal<RunUsage | null>(null);
  // FormControl ghi thẳng vào DOM: xóa ô nhập chắc chắn kể cả khi Enter tới trước change detection.
  protected readonly inputCtrl = new FormControl('', { nonNullable: true });

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

  protected readonly starterList = computed<string[]>(() => {
    const configured = this.agent()?.current?.spec?.starter_prompts ?? [];
    if (configured.length > 0) return configured;
    const name = (this.agent()?.name ?? '').toLowerCase();
    if (name.includes('master')) {
      return [
        'Báo cáo giờ hệ thống hiện tại và liệt kê các agent chuyên trách đang sẵn sàng',
        'Kiểm tra cấu trúc các bảng dữ liệu trong cơ sở dữ liệu và tóm tắt',
        'Phân tích yêu cầu hỗ trợ nội bộ và giao việc cho agent phù hợp',
        'Hướng dẫn tôi cách phối hợp nhiều agent để xử lý quy trình tự động',
      ];
    }
    return [
      'Giới thiệu ngắn gọn về vai trò và các kỹ năng bạn có thể thực hiện',
      'Bạn được cấp quyền sử dụng những công cụ (tool) nào?',
      'Thực hiện một tác vụ mẫu theo đúng quy tắc của bạn',
    ];
  });

  private abort: AbortController | null = null;

  ngOnInit(): void {
    this.api.get(this.id()).subscribe({
      next: (a) => {
        this.agent.set(a);
        const first = this.q()?.trim();
        if (first) {
          this.inputCtrl.setValue(first);
          void this.send();
        }
      },
      error: () => this.error.set('Không tải được agent.'),
    });
    this.loadThreads();
    const thread = this.thread();
    if (thread) this.openThread({ id: thread } as ThreadInfo);
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
      this.attachPendingApprovals(t.id);
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
    this.inputCtrl.setValue(text);
    void this.send();
  }

  protected stop(): void {
    this.abort?.abort();
  }

  protected async send(): Promise<void> {
    const message = this.inputCtrl.value.trim();
    if (!message || this.busy()) return;
    this.inputCtrl.setValue('');
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

    this.abort = new AbortController();
    await this.consume(
      this.stream.chat(
        {
          agentId: this.id(),
          message,
          threadId: this.threadId(),
          draft: this.isDraft(),
        },
        this.abort.signal,
      ),
      reply,
    );
  }

  /** Đọc luồng RunEvent vào một tin trả lời (dùng cho chat và resume). */
  private async consume(
    events: AsyncGenerator<RunEventMessage>,
    reply: ChatItem,
  ): Promise<void> {
    const touch = (): void => this.items.update((list) => [...list]);
    try {
      for await (const ev of events) {
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
            // Resume: tool_use đã hiện ở tin trước (đang chờ duyệt) → cập nhật bước đó.
            if (!this.findStep(String(d['id']))) {
              reply.tools.push({
                id: String(d['id']),
                name: String(d['name']),
                status: 'running',
              });
            }
            break;
          case 'tool_end': {
            const step = this.findStep(String(d['id']));
            if (step) {
              step.status = d['is_error'] ? 'error' : 'ok';
              step.name = String(d['name'] ?? step.name);
              step.preview = d['preview'] as string | undefined;
              step.durationMs = d['duration_ms'] as number | undefined;
              step.citations = d['citations'] as Citation[] | undefined;
            }
            break;
          }
          case 'delegation_start': {
            const existing = this.findDelegation(String(d['delegation_id']));
            if (existing) {
              existing.status = 'running';
            } else {
              (reply.delegations ??= []).push({
                id: String(d['delegation_id']),
                toolUseId: String(d['tool_use_id']),
                agentName: String(d['agent_name'] ?? 'Agent'),
                task: String(d['task'] ?? ''),
                status: 'running',
                steps: [],
              });
            }
            break;
          }
          case 'delegation_progress': {
            const del = this.findDelegation(String(d['delegation_id']));
            if (!del) break;
            const stepId = String(d['id']);
            if (d['event'] === 'tool_start') {
              del.steps.push({
                id: stepId,
                name: String(d['name']),
                status: 'running',
              });
            } else {
              const st = del.steps.find((x) => x.id === stepId);
              if (st) {
                st.status = d['is_error'] ? 'error' : 'ok';
                st.name = String(d['name'] ?? st.name);
              }
            }
            break;
          }
          case 'delegation_end': {
            const del = this.findDelegation(String(d['delegation_id']));
            if (del) {
              del.status = d['status'] as DelegationView['status'];
              del.summary = String(d['summary'] ?? '');
            }
            break;
          }
          case 'approval_required':
            reply.approvals = (
              (d['requests'] as ApprovalRequest[] | undefined) ?? []
            ).map((r) => this.cardFromRequest(r));
            break;
          case 'done':
            if (d['message_id']) reply.messageId = Number(d['message_id']);
            break;
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

  protected async decide(
    item: ChatItem,
    card: ApprovalCard,
    approve: boolean,
  ): Promise<void> {
    card.deciding = true;
    this.items.update((list) => [...list]);
    this.approvalsApi.decide(card.id, approve).subscribe({
      next: (a) => {
        card.status = a.status;
        card.deciding = false;
        this.afterDecision(item);
      },
      error: (e: { error?: { error?: { message?: string } } }) => {
        card.deciding = false;
        this.error.set(e.error?.error?.message ?? 'Không ghi được quyết định.');
        this.items.update((list) => [...list]);
      },
    });
  }

  /** Tiếp tục run khi mọi yêu cầu duyệt đã có quyết định. */
  protected async resume(item?: ChatItem): Promise<void> {
    const threadId = this.threadId();
    if (!threadId || this.busy()) return;
    if (item) item.canResume = false;
    this.error.set(null);
    this.busy.set(true);
    const reply: ChatItem = {
      role: 'assistant',
      text: '',
      tools: [],
      streaming: true,
    };
    this.items.update((list) => [...list, reply]);
    this.abort = new AbortController();
    await this.consume(this.stream.resume(threadId, this.abort.signal), reply);
  }

  private afterDecision(item: ChatItem): void {
    const cards = item.approvals ?? [];
    const allDecided = cards.every((c) => c.status !== 'pending');
    item.canResume = allDecided;
    this.items.update((list) => [...list]);
    // Người yêu cầu tự duyệt xong → chạy tiếp ngay.
    if (allDecided) void this.resume(item);
  }

  private findDelegation(id: string): DelegationView | undefined {
    for (const item of this.items()) {
      const found = item.delegations?.find((x) => x.id === id);
      if (found) return found;
    }
    return undefined;
  }

  /** Ẩn bước tool `delegate__*` khi đã có thẻ giao việc tương ứng. */
  /** Đánh giá câu trả lời (ADR-0020). */
  protected rate(m: ChatItem, rating: 1 | -1): void {
    const thread = this.threadId();
    if (!thread || !m.messageId) return;
    this.channelsApi.feedback(thread, m.messageId, rating).subscribe({
      next: () => {
        m.rating = rating;
        this.items.update((l) => [...l]);
      },
      error: () => this.error.set('Không gửi được đánh giá.'),
    });
  }

  /** Nguồn trích dẫn từ kb.search trong tin trả lời (ADR-0019). */
  protected citations(m: ChatItem): Citation[] {
    return m.tools.flatMap((t) => t.citations ?? []);
  }

  protected visibleTools(m: ChatItem): ToolStep[] {
    const shown = new Set((m.delegations ?? []).map((x) => x.toolUseId));
    return m.tools.filter((t) => !shown.has(t.id));
  }

  private findStep(id: string): ToolStep | undefined {
    for (const item of this.items()) {
      const step = item.tools.find((t) => t.id === id);
      if (step) return step;
    }
    return undefined;
  }

  private canDecide(roles: string[]): boolean {
    if (this.session.hasAnyRole('owner')) return true;
    return roles.length === 0 || this.session.hasAnyRole(...(roles as Role[]));
  }

  private cardFromRequest(r: ApprovalRequest): ApprovalCard {
    return {
      id: r.approval_id,
      toolUseId: r.tool_use_id,
      tool: r.tool,
      risk: r.risk,
      reason: r.reason,
      arguments: r.arguments ?? {},
      approverRoles: r.approver_roles ?? [],
      status: 'pending',
      canDecide: this.canDecide(r.approver_roles ?? []),
      agentName: (r as ApprovalRequest & { agent_name?: string }).agent_name,
    };
  }

  private cardFromApproval(a: Approval): ApprovalCard {
    return {
      id: a.id,
      toolUseId: a.tool_use_id,
      tool: a.tool,
      risk: a.risk,
      reason: a.reason,
      arguments: a.arguments,
      approverRoles: a.approver_roles,
      status: a.status,
      canDecide: a.can_decide,
      note: a.decision_note,
    };
  }

  /** Hội thoại đang dừng chờ duyệt: gắn thẻ duyệt vào tin cuối của agent. */
  private attachPendingApprovals(threadId: string): void {
    const last = this.items().at(-1);
    if (!last || last.role !== 'assistant') return;
    this.approvalsApi.pending(threadId).subscribe((state) => {
      if (!state.waiting) return;
      last.approvals = state.approvals.map((a) => this.cardFromApproval(a));
      last.canResume = state.can_resume;
      this.items.update((l) => [...l]);
    });
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

import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import {
  AgentsApi,
  AgentSummary,
  OpsApi,
  OpsChannel,
  OpsFeedback,
  OpsFilter,
  OpsThread,
  OpsTranscript,
  OpsUsage,
  SessionService,
} from '@aiagent/core';
import { Notice } from '@aiagent/ui';

type Tab = 'threads' | 'feedback' | 'usage';

const CHANNEL_LABEL: Record<OpsChannel, string> = {
  app: 'Ứng dụng',
  share: 'Link / widget',
  api: 'API',
  telegram: 'Telegram',
};

interface Group {
  key: string;
  label: string;
  runs: number;
  cost: number;
  tokens: number;
}

@Component({
  selector: 'app-ops',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    MatButtonModule,
    MatIcon,
    MatProgressBarModule,
    Notice,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="dashboard" />
        </span>
        <div class="hero-copy">
          <h1>Vận hành</h1>
          <p class="subtitle">
            Nhật ký hội thoại mọi kênh, phản hồi của người dùng và chi phí
            model. Mở toàn văn hội thoại của người khác được ghi audit.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <button
          mat-stroked-button
          class="pill-btn"
          type="button"
          [disabled]="loading()"
          (click)="reload()"
        >
          <mat-icon svgIcon="refresh" /> Tải lại
        </button>
      </div>
    </div>

    <div class="filters">
      <label>
        <span>Agent</span>
        <select
          [ngModel]="agentId()"
          (ngModelChange)="setAgent($event)"
          aria-label="Lọc theo agent"
        >
          @if (seeAll()) {
            <option value="">Tất cả agent</option>
          }
          @for (a of agentOptions(); track a.id) {
            <option [value]="a.id">{{ a.name }}</option>
          }
        </select>
      </label>
      <label>
        <span>Kênh</span>
        <select
          [ngModel]="channel()"
          (ngModelChange)="channel.set($event); reload()"
          aria-label="Lọc theo kênh"
        >
          <option value="">Mọi kênh</option>
          @for (c of channels; track c) {
            <option [value]="c">{{ label(c) }}</option>
          }
        </select>
      </label>
      <label>
        <span>Thời gian</span>
        <select
          [ngModel]="days()"
          (ngModelChange)="days.set(+$event); reload()"
          aria-label="Khoảng thời gian"
        >
          <option [value]="1">24 giờ</option>
          <option [value]="7">7 ngày</option>
          <option [value]="30">30 ngày</option>
          <option [value]="90">90 ngày</option>
        </select>
      </label>
      <div class="sub-tabs" role="tablist" aria-label="Mục">
        <button
          type="button"
          role="tab"
          [class.active]="tab() === 'threads'"
          (click)="setTab('threads')"
        >
          Hội thoại
        </button>
        <button
          type="button"
          role="tab"
          [class.active]="tab() === 'feedback'"
          (click)="setTab('feedback')"
        >
          Phản hồi
        </button>
        <button
          type="button"
          role="tab"
          [class.active]="tab() === 'usage'"
          (click)="setTab('usage')"
        >
          Chi phí
        </button>
      </div>
    </div>

    @if (error(); as e) {
      <ui-notice severity="error">{{ e }}</ui-notice>
    }
    @if (!seeAll() && agentOptions().length === 0) {
      <ui-notice severity="info"
        >Bạn chưa sở hữu agent nào để xem nhật ký.</ui-notice
      >
    }
    @if (loading()) {
      <mat-progress-bar mode="indeterminate" />
    }

    @switch (tab()) {
      @case ('threads') {
        <div class="split" [class.open]="!!transcript()">
          <div class="card list">
            @for (t of threads(); track t.id) {
              <button
                type="button"
                class="row"
                [class.sel]="transcript()?.id === t.id"
                (click)="open(t.id)"
              >
                <div class="row-main">
                  <strong>{{ t.title || '(không tiêu đề)' }}</strong>
                  <span class="muted small">
                    {{ agentName(t.agent_id) }} · {{ t.user_id }}
                  </span>
                </div>
                <div class="row-meta">
                  <span class="chip" [attr.data-ch]="t.channel">{{
                    label(t.channel)
                  }}</span>
                  <span class="muted small"
                    >{{ t.message_count }} tin ·
                    {{ t.cost_usd | number: '1.2-4' }} $</span
                  >
                  @if (t.thumbs_down) {
                    <span class="bad small"
                      ><mat-icon class="xs" svgIcon="thumb_down" />
                      {{ t.thumbs_down }}</span
                    >
                  }
                  @if (t.thumbs_up) {
                    <span class="good small"
                      ><mat-icon class="xs" svgIcon="thumb_up" />
                      {{ t.thumbs_up }}</span
                    >
                  }
                  <span class="muted small">{{
                    t.updated_at | date: 'dd/MM HH:mm'
                  }}</span>
                </div>
              </button>
            } @empty {
              <p class="empty">Không có hội thoại trong khoảng này.</p>
            }
            @if (hasMore()) {
              <button
                mat-stroked-button
                class="pill-btn more"
                type="button"
                (click)="loadMore()"
              >
                Xem thêm
              </button>
            }
          </div>

          @if (transcript(); as tr) {
            <aside class="card transcript" aria-label="Toàn văn hội thoại">
              <header>
                <div>
                  <strong>{{ tr.title || '(không tiêu đề)' }}</strong>
                  <div class="muted small">
                    {{ agentName(tr.agent_id) }} v{{ tr.agent_version }} ·
                    {{ label(tr.channel) }} · {{ tr.user_id }}
                  </div>
                </div>
                <button
                  mat-icon-button
                  type="button"
                  aria-label="Đóng"
                  (click)="transcript.set(null)"
                >
                  <mat-icon svgIcon="close" />
                </button>
              </header>
              @for (m of tr.messages; track m.id) {
                <div class="msg" [class.user]="m.role === 'user'">
                  <div class="who small muted">
                    {{ m.role === 'user' ? 'Người dùng' : 'Agent' }} ·
                    {{ m.created_at | date: 'HH:mm:ss' }}
                  </div>
                  @for (p of m.parts; track $index) {
                    @switch (p.type) {
                      @case ('text') {
                        <div class="text">{{ p.text }}</div>
                      }
                      @case ('tool_use') {
                        <div class="tool">
                          <code>{{ p.name }}</code>
                          <code class="args">{{ p.input }}</code>
                        </div>
                      }
                      @case ('tool_result') {
                        <div class="tool result" [class.err]="p.is_error">
                          <code>{{ p.text }}</code>
                        </div>
                      }
                    }
                  }
                  @for (f of feedbackFor(tr, m.id); track f.user_id) {
                    <div
                      class="fb"
                      [class.bad]="f.rating < 0"
                      [class.good]="f.rating > 0"
                    >
                      <mat-icon
                        class="xs"
                        [svgIcon]="f.rating > 0 ? 'thumb_up' : 'thumb_down'"
                      />
                      {{ f.comment || (f.rating > 0 ? 'Hữu ích' : 'Chưa tốt') }}
                    </div>
                  }
                </div>
              }
            </aside>
          }
        </div>
      }

      @case ('feedback') {
        <div class="sub-tabs inline" role="tablist" aria-label="Lọc đánh giá">
          <button
            type="button"
            [class.active]="rating() === null"
            (click)="setRating(null)"
          >
            Tất cả
          </button>
          <button
            type="button"
            [class.active]="rating() === -1"
            (click)="setRating(-1)"
          >
            👎 Chưa tốt
          </button>
          <button
            type="button"
            [class.active]="rating() === 1"
            (click)="setRating(1)"
          >
            👍 Hữu ích
          </button>
        </div>
        <div class="fb-grid">
          @for (f of feedback(); track f.id) {
            <article class="card fb-card">
              <header>
                <span [class.bad]="f.rating < 0" [class.good]="f.rating > 0">
                  <mat-icon
                    class="xs"
                    [svgIcon]="f.rating > 0 ? 'thumb_up' : 'thumb_down'"
                  />
                </span>
                <span class="muted small">
                  {{ agentName(f.agent_id) }} · {{ label(f.channel) }} ·
                  {{ f.created_at | date: 'dd/MM HH:mm' }}
                </span>
              </header>
              @if (f.comment) {
                <p class="comment">“{{ f.comment }}”</p>
              }
              <p class="answer small">
                {{ f.answer || '(câu trả lời không có văn bản)' }}
              </p>
              <button
                mat-button
                type="button"
                (click)="openFromFeedback(f.thread_id)"
              >
                Xem hội thoại
              </button>
            </article>
          } @empty {
            <p class="empty">Chưa có phản hồi.</p>
          }
        </div>
      }

      @case ('usage') {
        @if (usage(); as u) {
          <div class="kpis">
            <div class="card kpi">
              <span class="muted small">Lượt chạy</span
              ><strong>{{ u.runs | number }}</strong>
            </div>
            <div class="card kpi">
              <span class="muted small">Tỉ lệ lỗi</span>
              <strong
                >{{
                  u.runs
                    ? ((u.failed_runs / u.runs) * 100 | number: '1.0-1')
                    : 0
                }}%</strong
              >
            </div>
            <div class="card kpi">
              <span class="muted small">Token vào / ra</span>
              <strong
                >{{ u.input_tokens | number }} /
                {{ u.output_tokens | number }}</strong
              >
            </div>
            <div class="card kpi">
              <span class="muted small">Chi phí ước tính</span
              ><strong>{{ u.cost_usd | number: '1.2-4' }} $</strong>
            </div>
          </div>

          <div class="card chart">
            <h3>Chi phí theo ngày</h3>
            <div
              class="bars"
              role="img"
              [attr.aria-label]="
                'Chi phí theo ngày, tổng ' + u.cost_usd + ' USD'
              "
            >
              @for (d of daily(); track d.day) {
                <div
                  class="bar"
                  [title]="
                    d.day +
                    ': ' +
                    (d.cost | number: '1.2-4') +
                    ' $ · ' +
                    d.runs +
                    ' lượt'
                  "
                >
                  <span [style.height.%]="d.pct"></span>
                </div>
              } @empty {
                <p class="empty">Chưa có lượt chạy.</p>
              }
            </div>
          </div>

          <div class="groups">
            @for (g of [byAgent(), byChannel()]; track $index) {
              <div class="card">
                <h3>{{ $index === 0 ? 'Theo agent' : 'Theo kênh' }}</h3>
                <table class="simple">
                  <thead>
                    <tr>
                      <th>Tên</th>
                      <th>Lượt</th>
                      <th>Token</th>
                      <th>Chi phí ($)</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (r of g; track r.key) {
                      <tr>
                        <td>{{ r.label }}</td>
                        <td>{{ r.runs | number }}</td>
                        <td>{{ r.tokens | number }}</td>
                        <td>{{ r.cost | number: '1.2-4' }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            }
          </div>
        }
      }
    }
  `,
  styles: `
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: flex-end;
      margin-bottom: 0.9rem;
    }
    .filters label {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      font-size: 0.78rem;
      color: var(--app-muted);
    }
    .filters select {
      font: inherit;
      font-size: 0.86rem;
      padding: 0.35rem 0.6rem;
      border-radius: 10px;
      border: 1px solid var(--app-border);
      background: var(--app-surface);
      color: var(--app-text);
      min-width: 10rem;
    }
    .sub-tabs {
      display: flex;
      gap: 0.35rem;
      margin-left: auto;
      flex-wrap: wrap;
    }
    .sub-tabs.inline {
      margin: 0 0 0.75rem;
    }
    .sub-tabs button {
      border: 1px solid transparent;
      background: transparent;
      color: var(--app-muted);
      padding: 0.35rem 0.85rem;
      border-radius: 999px;
      font: inherit;
      font-size: 0.84rem;
      cursor: pointer;
    }
    .sub-tabs button.active {
      background: var(--app-surface);
      color: var(--app-text);
      font-weight: 600;
      border-color: var(--app-border);
      box-shadow: var(--app-shadow-sm);
    }
    .card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 0.9rem;
    }
    .card h3 {
      margin: 0 0 0.6rem;
      font-size: 0.92rem;
    }
    .split {
      display: grid;
      gap: 0.9rem;
      grid-template-columns: 1fr;
    }
    .split.open {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr);
    }
    @media (max-width: 960px) {
      .split.open {
        grid-template-columns: 1fr;
      }
    }
    .list {
      padding: 0.35rem;
      display: flex;
      flex-direction: column;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      text-align: left;
      border: none;
      background: transparent;
      padding: 0.6rem 0.7rem;
      border-radius: 12px;
      cursor: pointer;
      font: inherit;
      color: var(--app-text);
    }
    .row:hover,
    .row.sel {
      background: var(--app-surface-2);
    }
    .row-main {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .row-main strong {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 28rem;
    }
    .row-meta {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .chip {
      font-size: 0.72rem;
      padding: 0.1rem 0.5rem;
      border-radius: 999px;
      background: var(--app-surface-2);
    }
    .good {
      color: #16a34a;
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
    }
    .bad {
      color: #dc2626;
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
    }
    mat-icon.xs {
      width: 16px;
      height: 16px;
      font-size: 16px;
    }
    .more {
      align-self: center;
      margin: 0.5rem;
    }
    .empty {
      padding: 2rem 1rem;
      text-align: center;
      color: var(--app-muted);
    }
    .transcript {
      max-height: 75vh;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 0.7rem;
    }
    .transcript header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.5rem;
    }
    .msg {
      border-left: 3px solid var(--app-border);
      padding-left: 0.7rem;
    }
    .msg.user {
      border-left-color: var(--app-accent, #e11d74);
    }
    .text {
      white-space: pre-wrap;
      line-height: 1.5;
    }
    .tool {
      margin: 0.3rem 0;
      font-size: 0.8rem;
    }
    .tool code {
      white-space: pre-wrap;
      word-break: break-all;
    }
    .tool .args,
    .tool.result code {
      color: var(--app-muted);
    }
    .tool.err code {
      color: #dc2626;
    }
    .fb {
      margin-top: 0.35rem;
      font-size: 0.82rem;
    }
    .fb-grid {
      display: grid;
      gap: 0.8rem;
      grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    }
    .fb-card header {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .comment {
      font-weight: 500;
      margin: 0.5rem 0;
    }
    .answer {
      color: var(--app-muted);
      white-space: pre-wrap;
      max-height: 8rem;
      overflow: hidden;
      margin: 0.3rem 0;
    }
    .kpis {
      display: grid;
      gap: 0.8rem;
      grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
      margin-bottom: 0.9rem;
    }
    .kpi {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .kpi strong {
      font-size: 1.35rem;
    }
    .chart {
      margin-bottom: 0.9rem;
    }
    .bars {
      display: flex;
      align-items: flex-end;
      gap: 3px;
      height: 140px;
    }
    .bar {
      flex: 1;
      height: 100%;
      display: flex;
      align-items: flex-end;
      min-width: 4px;
    }
    .bar span {
      width: 100%;
      min-height: 2px;
      border-radius: 4px 4px 0 0;
      background: var(--app-accent, #e11d74);
    }
    .groups {
      display: grid;
      gap: 0.8rem;
      grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
    }
    table.simple {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    table.simple th,
    table.simple td {
      text-align: left;
      padding: 0.35rem 0.4rem;
      border-bottom: 1px solid var(--app-border);
    }
    table.simple th:not(:first-child),
    table.simple td:not(:first-child) {
      text-align: right;
    }
  `,
})
export class Ops implements OnInit {
  private readonly api = inject(OpsApi);
  private readonly agentsApi = inject(AgentsApi);
  private readonly session = inject(SessionService);

  protected readonly channels: OpsChannel[] = [
    'app',
    'share',
    'api',
    'telegram',
  ];
  protected readonly tab = signal<Tab>('threads');
  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly agentId = signal('');
  protected readonly channel = signal<OpsChannel | ''>('');
  protected readonly days = signal(7);
  protected readonly rating = signal<1 | -1 | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly threads = signal<OpsThread[]>([]);
  protected readonly hasMore = signal(false);
  protected readonly transcript = signal<OpsTranscript | null>(null);
  protected readonly feedback = signal<OpsFeedback[]>([]);
  protected readonly usage = signal<OpsUsage | null>(null);

  protected readonly seeAll = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin', 'auditor');
  });
  protected readonly agentOptions = computed(() =>
    this.seeAll() ? this.agents() : this.agents().filter((a) => a.can_edit),
  );

  protected readonly daily = computed(() => {
    const u = this.usage();
    if (!u) return [];
    const map = new Map<string, { cost: number; runs: number }>();
    const start = new Date(u.since);
    const end = new Date(u.until);
    for (
      let d = new Date(start.toISOString().slice(0, 10));
      d <= end;
      d.setUTCDate(d.getUTCDate() + 1)
    ) {
      map.set(d.toISOString().slice(0, 10), { cost: 0, runs: 0 });
    }
    for (const r of u.rows) {
      const e = map.get(r.day) ?? { cost: 0, runs: 0 };
      e.cost += r.cost_usd;
      e.runs += r.runs;
      map.set(r.day, e);
    }
    const max = Math.max(0, ...[...map.values()].map((v) => v.cost));
    return [...map.entries()].map(([day, v]) => ({
      day,
      ...v,
      pct: max > 0 ? (v.cost / max) * 100 : 0,
    }));
  });
  protected readonly byAgent = computed(() =>
    this.group(
      (r) => r.agent_id,
      (k) => this.agentName(k),
    ),
  );
  protected readonly byChannel = computed(() =>
    this.group(
      (r) => r.channel,
      (k) => this.label(k as OpsChannel),
    ),
  );

  ngOnInit(): void {
    this.agentsApi.list().subscribe({
      next: (list) => {
        this.agents.set(list);
        void this.session
          .load()
          .catch(() => undefined)
          .then(() => {
            if (!this.seeAll() && !this.agentId())
              this.agentId.set(this.agentOptions()[0]?.id ?? '');
            this.reload();
          });
      },
      error: () => this.error.set('Không tải được danh sách agent.'),
    });
  }

  protected label(c: OpsChannel): string {
    return CHANNEL_LABEL[c] ?? c;
  }

  protected agentName(id: string): string {
    return this.agents().find((a) => a.id === id)?.name ?? id.slice(0, 8);
  }

  protected feedbackFor(tr: OpsTranscript, messageId: number) {
    return tr.feedback.filter((f) => f.message_id === messageId);
  }

  protected setAgent(id: string): void {
    this.agentId.set(id);
    this.transcript.set(null);
    this.reload();
  }

  protected setTab(t: Tab): void {
    this.tab.set(t);
    this.reload();
  }

  protected setRating(r: 1 | -1 | null): void {
    this.rating.set(r);
    this.reload();
  }

  private filter(): OpsFilter {
    const since = new Date(Date.now() - this.days() * 86_400_000).toISOString();
    return {
      agent_id: this.agentId() || null,
      channel: this.channel() || null,
      since,
      rating: this.tab() === 'feedback' ? this.rating() : null,
    };
  }

  protected reload(): void {
    if (!this.seeAll() && !this.agentId()) return;
    this.loading.set(true);
    this.error.set(null);
    const done = {
      complete: () => this.loading.set(false),
      error: (e: unknown) => this.fail(e),
    };
    const f = this.filter();
    switch (this.tab()) {
      case 'threads':
        this.api.threads(f).subscribe({
          next: (t) => {
            this.threads.set(t);
            this.hasMore.set(t.length === 50);
          },
          ...done,
        });
        break;
      case 'feedback':
        this.api
          .feedback(f)
          .subscribe({ next: (x) => this.feedback.set(x), ...done });
        break;
      case 'usage':
        this.api
          .usage(f)
          .subscribe({ next: (x) => this.usage.set(x), ...done });
        break;
    }
  }

  protected loadMore(): void {
    const last = this.threads().at(-1);
    if (!last) return;
    this.api.threads(this.filter(), 50, last.updated_at).subscribe({
      next: (t) => {
        this.threads.update((cur) => [...cur, ...t]);
        this.hasMore.set(t.length === 50);
      },
      error: (e) => this.fail(e),
    });
  }

  protected open(threadId: string): void {
    this.api.transcript(threadId).subscribe({
      next: (t) => this.transcript.set(t),
      error: (e) => this.fail(e),
    });
  }

  protected openFromFeedback(threadId: string): void {
    this.tab.set('threads');
    this.reload();
    this.open(threadId);
  }

  private fail(e: unknown): void {
    this.loading.set(false);
    const detail = (e as { error?: { detail?: unknown } })?.error?.detail;
    this.error.set(
      typeof detail === 'string' ? detail : 'Không tải được dữ liệu vận hành.',
    );
  }

  private group(
    keyOf: (r: OpsUsage['rows'][number]) => string,
    labelOf: (k: string) => string,
  ): Group[] {
    const map = new Map<string, Group>();
    for (const r of this.usage()?.rows ?? []) {
      const k = keyOf(r);
      const g = map.get(k) ?? {
        key: k,
        label: labelOf(k),
        runs: 0,
        cost: 0,
        tokens: 0,
      };
      g.runs += r.runs;
      g.cost += r.cost_usd;
      g.tokens += r.input_tokens + r.output_tokens;
      map.set(k, g);
    }
    return [...map.values()].sort((a, b) => b.cost - a.cost);
  }
}

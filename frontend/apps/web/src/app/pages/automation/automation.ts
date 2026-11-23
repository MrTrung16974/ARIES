import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import {
  AgentsApi,
  AgentSummary,
  AutomationApi,
  ConnectionInfo,
  ConnectionsApi,
  ConnectorInfo,
  CreateSchedule,
  ScheduleInfo,
  ScheduleRunInfo,
  SessionService,
} from '@aiagent/core';
import { Notice } from '@aiagent/ui';
import { fieldsOf, toConfig } from '../connections/connections';

/** Icon theo kiểu kích hoạt (connector có trigger) — ADR-0023. */
const KIND_ICON: Record<string, string> = {
  cron: 'schedule',
  gmail: 'mail',
  kafka: 'hub',
};

const PRESETS = [
  { label: '8:00 sáng mỗi ngày làm việc', cron: '0 8 * * 1-5' },
  { label: '8:00 sáng thứ Hai', cron: '0 8 * * 1' },
  { label: 'Mỗi giờ', cron: '0 * * * *' },
  { label: '17:30 mỗi ngày', cron: '30 17 * * *' },
  { label: 'Ngày 1 hằng tháng, 9:00', cron: '0 9 1 * *' },
  { label: 'Tùy chỉnh…', cron: '' },
];

const STATUS: Record<string, string> = {
  completed: 'Hoàn tất',
  failed: 'Lỗi',
  refused: 'Bị từ chối',
  awaiting_approval: 'Chờ duyệt',
  running: 'Đang chạy',
  idle: 'Không có sự kiện mới',
  cancelled: 'Đã hủy',
};

@Component({
  selector: 'app-automation',
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatIcon,
    RouterLink,
    Notice,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true"
          ><mat-icon class="sm" svgIcon="schedule"
        /></span>
        <div class="hero-copy">
          <h1>Tự động hóa</h1>
          <p class="subtitle">
            Cho agent tự chạy theo lịch hoặc khi có sự kiện mới (mail, message
            Kafka…). Chạy dưới tên bạn
            nhưng không mang quyền quản trị; thao tác cần duyệt sẽ chờ bạn ở
            trang Duyệt — không có gì tự gửi đi.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        @if (seeAll()) {
          <label class="check"
            ><input
              type="checkbox"
              [ngModel]="everyone()"
              (ngModelChange)="everyone.set($event); reload()"
            />
            Mọi người</label
          >
        }
        <button
          mat-flat-button
          class="btn-aries"
          type="button"
          (click)="formOpen.set(!formOpen())"
        >
          <mat-icon svgIcon="add" /> Tạo lịch
        </button>
      </div>
    </div>

    @if (error(); as e) {
      <ui-notice severity="error">{{ e }}</ui-notice>
    }

    @if (formOpen()) {
      <form class="card form" (ngSubmit)="create()">
        <div class="grid">
          <label
            >Tên lịch
            <input
              name="name"
              [(ngModel)]="form.name"
              required
              maxlength="100"
              placeholder="Báo cáo đầu tuần"
            />
          </label>
          <label
            >Agent (đã publish)
            <select name="agent" [(ngModel)]="form.agent_id" required>
              @for (a of publishedAgents(); track a.id) {
                <option [value]="a.id">{{ a.name }}</option>
              }
            </select>
          </label>
          <label
            >Kích hoạt khi
            <select
              name="kind"
              [ngModel]="form.kind"
              (ngModelChange)="pickKind($event)"
            >
              <option value="cron">Đến giờ (lịch)</option>
              @for (t of triggerConnectors(); track t.id) {
                <option [value]="t.id">
                  {{ t.name }} — {{ t.triggers?.[0]?.description }}
                </option>
              }
            </select>
          </label>
        </div>

        @if (form.kind === 'cron') {
          <div class="grid">
            <label
              >Lịch
              <select
                name="preset"
                [ngModel]="preset"
                (ngModelChange)="pickPreset($event)"
              >
                @for (p of presets; track p.label) {
                  <option [value]="p.cron">{{ p.label }}</option>
                }
              </select>
            </label>
            <label
              >Biểu thức cron (phút giờ ngày tháng thứ)
              <input
                name="cron"
                [(ngModel)]="form.cron"
                required
                placeholder="0 8 * * 1-5"
              />
            </label>
            <label
              >Múi giờ
              <input name="tz" [(ngModel)]="form.timezone" />
            </label>
          </div>
        } @else {
          <div class="grid">
            <label
              >Kết nối {{ triggerConnector()?.name }}
              <select name="conn" [(ngModel)]="form.connection_id" required>
                @for (c of kindConnections(); track c.id) {
                  <option [value]="c.id">{{ c.name }}</option>
                }
              </select>
            </label>
            <label
              >Kiểm tra mỗi
              <select name="interval" [(ngModel)]="form.interval_minutes">
                @for (m of intervals; track m) {
                  <option [ngValue]="m">{{ m }} phút</option>
                }
              </select>
            </label>
            @for (f of triggerFields(); track f.key) {
              @if (f.kind === 'boolean') {
                <label class="check"
                  ><input
                    type="checkbox"
                    [name]="'tf_' + f.key"
                    [(ngModel)]="filterValues[f.key]"
                  />
                  {{ f.label }}</label
                >
              } @else if (f.kind === 'select') {
                <label
                  >{{ f.label }}
                  <select [name]="'tf_' + f.key" [(ngModel)]="filterValues[f.key]">
                    <option value=""></option>
                    @for (o of f.options; track o) {
                      <option [value]="o">{{ o }}</option>
                    }
                  </select>
                </label>
              } @else {
                <label
                  >{{ f.label }}{{ f.required ? ' *' : '' }}
                  <input
                    [name]="'tf_' + f.key"
                    [type]="f.kind === 'number' ? 'number' : 'text'"
                    [(ngModel)]="filterValues[f.key]"
                    [required]="f.required"
                  />
                </label>
              }
            }
          </div>
          @if (kindConnections().length === 0) {
            <ui-notice severity="info">
              Chưa có kết nối {{ triggerConnector()?.name }} — vào
              <a routerLink="/connections">Kết nối</a> để tạo.
            </ui-notice>
          }
          <p class="muted small">
            Nội dung sự kiện (mail, message…) được đưa cho agent dưới dạng
            <strong>dữ liệu không tin cậy</strong>: agent không làm theo chỉ dẫn
            trong đó, và mọi thao tác ghi/gửi đều phải duyệt.
          </p>
        }

        <label class="full"
          >Việc agent cần làm
          <textarea
            name="prompt"
            rows="3"
            [(ngModel)]="form.prompt"
            required
            maxlength="4000"
            [placeholder]="
              form.kind === 'gmail'
                ? 'Phân loại mail, tóm tắt 3 dòng và soạn nháp trả lời nếu là yêu cầu báo giá.'
                : form.kind === 'cron'
                  ? 'Tổng hợp các việc cần làm trong tuần và gửi cho tôi.'
                  : 'Đọc sự kiện, kiểm tra dữ liệu liên quan và tóm tắt cho tôi.'
            "
          ></textarea>
        </label>
        <div class="actions">
          <button mat-button type="button" (click)="formOpen.set(false)">
            Hủy
          </button>
          <button
            mat-flat-button
            class="btn-aries"
            type="submit"
            [disabled]="saving()"
          >
            Lưu lịch
          </button>
        </div>
      </form>
    }

    <div class="list">
      @for (s of schedules(); track s.id) {
        <article class="card item" [class.off]="!s.enabled">
          <header>
            <span class="kind" [attr.data-kind]="s.kind">
              <mat-icon
                class="xs"
                [svgIcon]="kindIcon(s.kind)"
              />
            </span>
            <div class="title">
              <strong>{{ s.name }}</strong>
              <span class="muted small"
                >{{ agentName(s.agent_id) }} · {{ describe(s) }}</span
              >
            </div>
            <div class="meta small">
              @if (s.enabled) {
                <span
                  >Lần tới:
                  {{
                    s.next_run_at ? (s.next_run_at | date: 'dd/MM HH:mm') : '—'
                  }}</span
                >
              } @else {
                <span class="muted"
                  >Đã tắt{{
                    s.failure_count >= 5 ? ' (lỗi liên tiếp)' : ''
                  }}</span
                >
              }
              @if (s.last_status) {
                <span class="status" [attr.data-status]="s.last_status">{{
                  label(s.last_status)
                }}</span>
              }
            </div>
          </header>
          <p class="prompt small">{{ s.prompt }}</p>
          <div class="actions">
            <button mat-button type="button" (click)="toggleRuns(s)">
              {{ openRuns() === s.id ? 'Ẩn lịch sử' : 'Lịch sử' }}
            </button>
            @if (s.owner_id === me()) {
              <button
                mat-button
                type="button"
                [disabled]="!s.enabled"
                (click)="runNow(s)"
              >
                <mat-icon svgIcon="play_arrow" /> Chạy ngay
              </button>
            }
            <button mat-button type="button" (click)="toggle(s)">
              {{ s.enabled ? 'Tắt' : 'Bật' }}
            </button>
            <button mat-button type="button" class="danger" (click)="remove(s)">
              Xóa
            </button>
          </div>
          @if (openRuns() === s.id) {
            <table class="runs">
              <tbody>
                @for (r of runs(); track r.id) {
                  <tr>
                    <td class="nowrap">
                      {{ r.started_at | date: 'dd/MM HH:mm' }}
                    </td>
                    <td>{{ r.title }}</td>
                    <td>
                      <span class="status" [attr.data-status]="r.status">{{
                        label(r.status)
                      }}</span>
                      @if (r.error) {
                        <span class="muted small">— {{ r.error }}</span>
                      }
                    </td>
                    <td class="right">
                      @if (r.thread_id && s.owner_id === me()) {
                        <a
                          mat-button
                          [routerLink]="['/agents', s.agent_id, 'chat']"
                          [queryParams]="{ thread: r.thread_id }"
                          >Mở hội thoại</a
                        >
                      }
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td class="muted small">Chưa chạy lần nào.</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </article>
      } @empty {
        <p class="empty">Chưa có lịch chạy nào. Bấm “Tạo lịch” để bắt đầu.</p>
      }
    </div>
  `,
  styles: `
    .card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1rem 1.1rem;
    }
    .form {
      margin-bottom: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .grid {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    }
    label {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      font-size: 0.8rem;
      color: var(--app-muted);
    }
    label.check {
      flex-direction: row;
      align-items: center;
      gap: 0.35rem;
    }
    input:not([type='checkbox']),
    select,
    textarea {
      font: inherit;
      font-size: 0.9rem;
      color: var(--app-text);
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: 10px;
      padding: 0.45rem 0.6rem;
    }
    textarea {
      resize: vertical;
    }
    .actions {
      display: flex;
      gap: 0.4rem;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .list {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .item header {
      display: flex;
      align-items: center;
      gap: 0.7rem;
      flex-wrap: wrap;
    }
    .item.off {
      opacity: 0.65;
    }
    .kind {
      width: 2rem;
      height: 2rem;
      border-radius: 10px;
      display: grid;
      place-items: center;
      background: var(--app-surface-2);
    }
    .title {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-width: 12rem;
    }
    .meta {
      display: flex;
      gap: 0.6rem;
      align-items: center;
      flex-wrap: wrap;
    }
    .prompt {
      color: var(--app-muted);
      white-space: pre-wrap;
      margin: 0.5rem 0;
      max-height: 4.5rem;
      overflow: hidden;
    }
    .status {
      font-size: 0.75rem;
      padding: 0.1rem 0.5rem;
      border-radius: 999px;
      background: var(--app-surface-2);
    }
    .status[data-status='failed'] {
      color: #dc2626;
    }
    .status[data-status='completed'] {
      color: #16a34a;
    }
    .status[data-status='awaiting_approval'] {
      color: #d97706;
    }
    .runs {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
      margin-top: 0.5rem;
    }
    .runs td {
      padding: 0.4rem;
      border-top: 1px solid var(--app-border);
      vertical-align: middle;
    }
    .right {
      text-align: right;
    }
    .nowrap {
      white-space: nowrap;
    }
    .danger {
      color: var(--app-danger, #dc2626);
    }
    mat-icon.xs {
      width: 18px;
      height: 18px;
    }
    .empty {
      padding: 2rem;
      text-align: center;
      color: var(--app-muted);
    }
  `,
})
export class Automation implements OnInit {
  private readonly api = inject(AutomationApi);
  private readonly agentsApi = inject(AgentsApi);
  private readonly connectionsApi = inject(ConnectionsApi);
  private readonly session = inject(SessionService);

  protected readonly presets = PRESETS;
  protected readonly intervals = [1, 2, 5, 10, 15, 30, 60];
  protected readonly connectors = signal<ConnectorInfo[]>([]);
  protected readonly schedules = signal<ScheduleInfo[]>([]);
  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly connections = signal<ConnectionInfo[]>([]);
  protected readonly runs = signal<ScheduleRunInfo[]>([]);
  protected readonly openRuns = signal<string | null>(null);
  protected readonly formOpen = signal(false);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly everyone = signal(false);

  protected preset = PRESETS[0].cron;
  protected form: CreateSchedule = this.blank();
  protected filterValues: Record<string, unknown> = {};

  protected readonly seeAll = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin', 'auditor');
  });
  protected readonly me = computed(() => this.session.me()?.subject ?? '');
  protected readonly publishedAgents = computed(() =>
    this.agents().filter(
      (a) => a.published_version !== null && a.status !== 'archived',
    ),
  );
  /** Connector có trigger → thành kiểu kích hoạt (gmail, kafka…). */
  protected readonly triggerConnectors = computed(() =>
    this.connectors().filter((c) => (c.triggers ?? []).length > 0),
  );
  private readonly kind = signal('cron');
  protected readonly triggerConnector = computed(() =>
    this.connectors().find((c) => c.id === this.kind()),
  );
  protected readonly triggerFields = computed(() => {
    const schema = this.triggerConnector()?.triggers?.[0]?.config_schema;
    return schema ? fieldsOf(schema) : [];
  });
  protected readonly kindConnections = computed(() =>
    this.connections().filter(
      (c) => c.connector_id === this.kind() && c.status === 'active',
    ),
  );

  ngOnInit(): void {
    void this.session.load().catch(() => undefined);
    this.agentsApi.list(true).subscribe((a) => this.agents.set(a));
    this.connectionsApi.connectors().subscribe({
      next: (c) => this.connectors.set(c),
      error: () => undefined,
    });
    this.connectionsApi.list().subscribe({
      next: (c) => this.connections.set(c),
      error: () => undefined,
    });
    this.reload();
  }

  protected reload(): void {
    this.api.list(this.everyone()).subscribe({
      next: (s) => this.schedules.set(s),
      error: (e) => this.fail(e),
    });
  }

  protected label(status: string): string {
    return STATUS[status] ?? status;
  }

  protected agentName(id: string): string {
    return this.agents().find((a) => a.id === id)?.name ?? id.slice(0, 8);
  }

  protected kindIcon(kind: string): string {
    return KIND_ICON[kind] ?? 'bolt';
  }

  protected describe(s: ScheduleInfo): string {
    if (s.kind !== 'cron') {
      const head = s.cron.split(' ')[0];
      const every =
        s.cron === '0 * * * *' ? 60 : head === '*' ? 1 : Number(head.replace('*/', ''));
      const name = this.connectors().find((c) => c.id === s.kind)?.name ?? s.kind;
      const filters = Object.values(s.trigger_config ?? {})
        .filter((v) => v !== '' && v !== null && v !== undefined)
        .map(String);
      return `${name} · mỗi ${every} phút${filters.length ? ' · lọc: ' + filters.join(', ') : ''}`;
    }
    const preset = PRESETS.find((p) => p.cron === s.cron);
    return `${preset ? preset.label : s.cron} (${s.timezone})`;
  }

  protected pickKind(kind: string): void {
    this.form.kind = kind;
    this.kind.set(kind);
    this.form.connection_id = null;
    this.filterValues = {};
  }

  protected pickPreset(cron: string): void {
    this.preset = cron;
    if (cron) this.form.cron = cron;
  }

  protected create(): void {
    this.saving.set(true);
    this.error.set(null);
    const body: CreateSchedule = { ...this.form };
    if (body.kind !== 'cron') {
      body.trigger_config = toConfig(this.triggerFields(), this.filterValues);
      delete body.cron;
    } else {
      delete body.connection_id;
    }
    this.api.create(body).subscribe({
      next: () => {
        this.saving.set(false);
        this.formOpen.set(false);
        this.form = this.blank();
        this.kind.set('cron');
        this.filterValues = {};
        this.reload();
      },
      error: (e) => {
        this.saving.set(false);
        this.fail(e);
      },
    });
  }

  protected toggle(s: ScheduleInfo): void {
    this.api
      .update(s.id, { enabled: !s.enabled })
      .subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected runNow(s: ScheduleInfo): void {
    this.api.runNow(s.id).subscribe({
      next: () => setTimeout(() => this.toggleRuns(s, true), 3000),
      error: (e) => this.fail(e),
    });
  }

  protected remove(s: ScheduleInfo): void {
    if (!confirm(`Xóa lịch “${s.name}” và lịch sử chạy của nó?`)) return;
    this.api
      .remove(s.id)
      .subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected toggleRuns(s: ScheduleInfo, force = false): void {
    if (this.openRuns() === s.id && !force) {
      this.openRuns.set(null);
      return;
    }
    this.openRuns.set(s.id);
    this.api
      .runs(s.id)
      .subscribe({ next: (r) => this.runs.set(r), error: (e) => this.fail(e) });
    this.reload();
  }

  private blank(): CreateSchedule {
    return {
      name: '',
      agent_id: '',
      kind: 'cron',
      prompt: '',
      cron: PRESETS[0].cron,
      timezone: 'Asia/Ho_Chi_Minh',
      interval_minutes: 5,
      connection_id: null,
    };
  }

  private fail(e: unknown): void {
    const msg = (e as { error?: { error?: { message?: string } } })?.error
      ?.error?.message;
    this.error.set(msg ?? 'Thao tác thất bại.');
  }
}

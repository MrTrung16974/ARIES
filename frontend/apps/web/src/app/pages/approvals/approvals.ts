import { DatePipe, JsonPipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { Approval, ApprovalsApi, ApprovalStatus } from '@aiagent/core';
import { Notice, Severity, Tag } from '@aiagent/ui';

const STATUS_LABEL: Record<ApprovalStatus, [string, Severity]> = {
  pending: ['Chờ duyệt', 'warn'],
  approved: ['Đã duyệt', 'success'],
  denied: ['Đã từ chối', 'danger'],
  expired: ['Hết hiệu lực', 'secondary'],
};

const RISK_LABEL: Record<string, string> = {
  read: 'Đọc',
  write: 'Ghi dữ liệu',
  external_send: 'Gửi ra ngoài',
  destructive: 'Phá hủy',
};

/** Hộp duyệt: yêu cầu của tôi; Admin/Owner thấy mọi yêu cầu trong tổ chức. */
@Component({
  selector: 'app-approvals',
  imports: [
    DatePipe,
    JsonPipe,
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="gavel" />
        </span>
        <div class="hero-copy">
          <h1>Duyệt thao tác</h1>
          <p class="subtitle">
            Agent sẽ tự động tạm dừng trước khi thực thi thao tác ghi, gửi dữ
            liệu ra ngoài hoặc phá hủy để chờ con người phê duyệt (HITL). Thao
            tác phá hủy chỉ Admin/Owner được phép duyệt.
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
          <mat-icon svgIcon="refresh" />
          {{ loading() ? 'Đang tải…' : 'Tải lại' }}
        </button>
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon warn" aria-hidden="true">
          <mat-icon class="sm" svgIcon="gavel" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ pendingCount() }}</span>
          <span class="kpi-label">Đang chờ duyệt</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="check_circle" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ approvedCount() }}</span>
          <span class="kpi-label">Đã cho phép</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ highRiskCount() }}</span>
          <span class="kpi-label">Rủi ro cao / Phá hủy</span>
        </div>
      </div>
    </div>

    <div class="sub-tabs" role="tablist" aria-label="Lọc trạng thái">
      @for (s of filters; track s.label) {
        <button
          type="button"
          [class.active]="status() === s.value"
          (click)="status.set(s.value); reload()"
        >
          <mat-icon class="sm" [svgIcon]="s.icon" aria-hidden="true" />
          {{ s.label }}
        </button>
      }
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }

    <div class="list">
      @for (a of items(); track a.id) {
        <article class="item" [attr.data-status]="a.status">
          <div class="title-line">
            <span class="icon-badge" aria-hidden="true">
              <mat-icon class="sm" svgIcon="gavel" />
            </span>
            <div class="title-info">
              <div class="title-tags">
                <code>{{ a.tool }}</code>
                <ui-tag
                  [value]="riskText(a.risk)"
                  [severity]="a.risk === 'destructive' ? 'danger' : 'warn'"
                />
                <ui-tag [value]="label(a)[0]" [severity]="label(a)[1]" />
              </div>
              <p class="reason">{{ a.reason }}</p>
            </div>
            <span class="muted small timestamp">{{
              a.created_at | date: 'dd/MM/yyyy HH:mm'
            }}</span>
          </div>

          <pre class="args">{{ a.arguments | json }}</pre>

          <div class="meta-bar">
            <div class="muted small meta">
              <span class="meta-chip">
                <mat-icon class="sm" svgIcon="person" aria-hidden="true" />
                Yêu cầu: {{ a.requester_id }}
              </span>
              @if (a.approver_roles.length) {
                <span class="meta-chip">
                  <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
                  Cần vai trò: {{ a.approver_roles.join(' / ') }}
                </span>
              }
              @if (a.decided_by) {
                <span class="meta-chip">
                  <mat-icon class="sm" svgIcon="verified" aria-hidden="true" />
                  Quyết định: {{ a.decided_by }}
                </span>
              }
              @if (a.decision_note) {
                <span class="meta-chip">Ghi chú: {{ a.decision_note }}</span>
              }
            </div>
            <a
              mat-stroked-button
              class="pill-btn"
              [routerLink]="['/agents', a.agent_id, 'chat']"
            >
              <mat-icon svgIcon="forum" /> Mở hội thoại
            </a>
          </div>

          @if (a.can_decide) {
            <div class="decide">
              <mat-form-field
                appearance="outline"
                subscriptSizing="dynamic"
                class="note"
              >
                <mat-label>Ghi chú quyết định (tùy chọn)</mat-label>
                <input
                  matInput
                  [(ngModel)]="notes[a.id]"
                  placeholder="Lý do cho phép hoặc từ chối…"
                  maxlength="500"
                />
              </mat-form-field>
              <div class="decide-btns">
                <button
                  mat-stroked-button
                  class="pill-btn"
                  type="button"
                  [disabled]="busyId() === a.id"
                  (click)="decide(a, false)"
                >
                  <mat-icon svgIcon="cancel" /> Từ chối
                </button>
                <button
                  mat-flat-button
                  class="btn-aries"
                  type="button"
                  [disabled]="busyId() === a.id"
                  (click)="decide(a, true)"
                >
                  <mat-icon svgIcon="check_circle" /> Cho phép thực thi
                </button>
              </div>
            </div>
          }
        </article>
      } @empty {
        <div class="empty-state-card">
          <div class="empty-icon-ring" aria-hidden="true">
            <mat-icon
              [svgIcon]="status() === 'pending' ? 'check_circle' : 'gavel'"
            />
          </div>
          <h2>
            {{
              status() === 'pending'
                ? 'Không có yêu cầu nào đang chờ duyệt'
                : 'Chưa có lịch sử duyệt thao tác'
            }}
          </h2>
          <p>
            {{
              status() === 'pending'
                ? 'Mọi thao tác nhạy cảm của AI Agent đều đã được xử lý. Yêu cầu mới sẽ tự động xuất hiện tại đây khi có hành động cần phê duyệt.'
                : 'Khi AI Agent yêu cầu thực thi thao tác ghi, gửi ra ngoài hoặc phá hủy dữ liệu, lịch sử phê duyệt sẽ được lưu lại tại đây.'
            }}
          </p>
        </div>
      }
    </div>
  `,
  styles: `
    .sub-tabs {
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
      margin-bottom: 1rem;
      button {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        border: 1px solid transparent;
        background: transparent;
        color: var(--app-muted);
        padding: 0.38rem 0.9rem;
        border-radius: 999px;
        font: inherit;
        font-size: 0.84rem;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.15s ease;
        &:hover {
          background: var(--app-surface-2);
          color: var(--app-text);
        }
        &.active {
          background: var(--app-surface);
          color: var(--app-text);
          font-weight: 600;
          border-color: var(--app-border);
          box-shadow: var(--app-shadow-sm);
          mat-icon {
            color: var(--app-primary);
          }
        }
      }
    }
    .list {
      display: flex;
      flex-direction: column;
      gap: 0.95rem;
    }
    .item {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-left: 4px solid #d97706;
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1.1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.7rem;
      &[data-status='approved'] {
        border-left-color: #16a34a;
      }
      &[data-status='denied'],
      &[data-status='expired'] {
        border-left-color: var(--app-muted);
      }
    }
    .title-line {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .title-info {
      flex: 1;
      min-width: 220px;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .title-tags {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .reason {
      margin: 0;
      font-size: 0.88rem;
      color: var(--app-text);
    }
    .timestamp {
      white-space: nowrap;
    }
    .args {
      margin: 0;
      padding: 0.65rem 0.85rem;
      max-height: 12rem;
      overflow: auto;
      font-size: 0.8rem;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-md);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .meta-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 1rem;
    }
    .meta-chip {
      display: inline-flex;
      align-items: center;
      gap: 0.28rem;
    }
    .decide {
      display: flex;
      gap: 0.65rem;
      align-items: center;
      flex-wrap: wrap;
      padding-top: 0.75rem;
      border-top: 1px solid var(--app-border);
    }
    .decide-btns {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .note {
      min-width: 240px;
      flex: 1;
    }
  `,
})
export class Approvals implements OnInit {
  private readonly api = inject(ApprovalsApi);

  protected readonly filters: {
    value: ApprovalStatus | undefined;
    label: string;
    icon: string;
  }[] = [
    { value: 'pending', label: 'Chờ duyệt', icon: 'gavel' },
    { value: undefined, label: 'Tất cả lịch sử', icon: 'dashboard' },
  ];
  protected readonly status = signal<ApprovalStatus | undefined>('pending');
  protected readonly items = signal<Approval[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly busyId = signal<string | null>(null);
  protected notes: Record<string, string> = {};

  protected readonly pendingCount = computed(
    () => this.items().filter((i) => i.status === 'pending').length,
  );
  protected readonly approvedCount = computed(
    () => this.items().filter((i) => i.status === 'approved').length,
  );
  protected readonly highRiskCount = computed(
    () =>
      this.items().filter(
        (i) => i.risk === 'destructive' || i.risk === 'external_send',
      ).length,
  );

  ngOnInit(): void {
    this.reload();
  }

  protected label(a: Approval): [string, Severity] {
    return STATUS_LABEL[a.status];
  }

  protected riskText(risk: string): string {
    return RISK_LABEL[risk] ?? risk;
  }

  protected reload(): void {
    this.loading.set(true);
    this.api.list({ status: this.status() }).subscribe({
      next: (list) => {
        this.items.set(list);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Không tải được danh sách duyệt.');
      },
    });
  }

  protected decide(a: Approval, approve: boolean): void {
    this.busyId.set(a.id);
    this.error.set(null);
    this.api.decide(a.id, approve, this.notes[a.id]).subscribe({
      next: () => {
        this.busyId.set(null);
        this.reload();
      },
      error: (err: { error?: { error?: { message?: string } } }) => {
        this.busyId.set(null);
        this.error.set(
          err.error?.error?.message ?? 'Không ghi được quyết định.',
        );
        this.reload();
      },
    });
  }
}

import { DatePipe, JsonPipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { Approval, ApprovalsApi, ApprovalStatus } from '@aiagent/core';
import { Notice, Severity, Tag } from '@aiagent/ui';

const STATUS_LABEL: Record<ApprovalStatus, [string, Severity]> = {
  pending: ['chờ duyệt', 'warn'],
  approved: ['đã duyệt', 'success'],
  denied: ['đã từ chối', 'danger'],
  expired: ['hết hiệu lực', 'secondary'],
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
    <div class="section-head">
      <div>
        <h1>Duyệt thao tác</h1>
        <p class="muted subtitle">
          Agent dừng lại trước thao tác ghi, gửi ra ngoài hoặc phá hủy dữ liệu cho tới khi
          có người duyệt. Thao tác phá hủy chỉ Admin/Owner được duyệt.
        </p>
      </div>
      <button mat-stroked-button type="button" (click)="reload()">
        <mat-icon svgIcon="refresh" /> Tải lại
      </button>
    </div>

    <div class="sub-tabs" role="tablist" aria-label="Lọc trạng thái">
      @for (s of filters; track s.value) {
        <button
          type="button"
          [class.active]="status() === s.value"
          (click)="status.set(s.value); reload()"
        >
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
            <mat-icon class="sm" svgIcon="gavel" aria-hidden="true" />
            <code>{{ a.tool }}</code>
            <ui-tag [value]="a.risk" [severity]="a.risk === 'destructive' ? 'danger' : 'warn'" />
            <ui-tag [value]="label(a)[0]" [severity]="label(a)[1]" />
            <span class="spacer"></span>
            <span class="muted small">{{ a.created_at | date: 'short' }}</span>
          </div>
          <p class="small">{{ a.reason }}</p>
          <pre class="args">{{ a.arguments | json }}</pre>
          <div class="muted small meta">
            <span>Người yêu cầu: {{ a.requester_id }}</span>
            @if (a.approver_roles.length) {
              <span>Cần vai trò: {{ a.approver_roles.join(' / ') }}</span>
            }
            @if (a.decided_by) {
              <span>Quyết định bởi: {{ a.decided_by }}</span>
            }
            @if (a.decision_note) {
              <span>Ghi chú: {{ a.decision_note }}</span>
            }
            <a [routerLink]="['/agents', a.agent_id, 'chat']">Mở agent</a>
          </div>
          @if (a.can_decide) {
            <div class="decide">
              <mat-form-field appearance="outline" subscriptSizing="dynamic" class="note">
                <mat-label>Ghi chú (tùy chọn)</mat-label>
                <input matInput [(ngModel)]="notes[a.id]" maxlength="500" />
              </mat-form-field>
              <button
                mat-flat-button
                class="btn-aries"
                type="button"
                [disabled]="busyId() === a.id"
                (click)="decide(a, true)"
              >
                <mat-icon svgIcon="check_circle" /> Cho phép
              </button>
              <button
                mat-stroked-button
                type="button"
                [disabled]="busyId() === a.id"
                (click)="decide(a, false)"
              >
                <mat-icon svgIcon="cancel" /> Từ chối
              </button>
            </div>
          }
        </article>
      } @empty {
        <div class="empty-box muted">Không có yêu cầu nào.</div>
      }
    </div>
  `,
  styles: `
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 0.85rem;
      h1 { font-size: 1.25rem; margin: 0 0 0.15rem; }
      .subtitle { margin: 0; font-size: 0.88rem; }
    }
    .sub-tabs {
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
      margin-bottom: 0.85rem;
      button {
        border: 1px solid transparent;
        background: transparent;
        color: var(--app-muted);
        padding: 0.35rem 0.85rem;
        border-radius: 999px;
        font: inherit;
        font-size: 0.84rem;
        cursor: pointer;
        &.active {
          background: var(--app-surface);
          color: var(--app-text);
          font-weight: 600;
          border-color: var(--app-border);
        }
      }
    }
    .list { display: flex; flex-direction: column; gap: 0.8rem; }
    .item {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-left: 4px solid #d97706;
      border-radius: var(--app-radius-lg);
      padding: 0.9rem 1.1rem;
      &[data-status='approved'] { border-left-color: #16a34a; }
      &[data-status='denied'], &[data-status='expired'] { border-left-color: var(--app-muted); }
      p { margin: 0.35rem 0; }
    }
    .title-line { display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .spacer { flex: 1; }
    .args {
      margin: 0.3rem 0;
      padding: 0.5rem;
      max-height: 12rem;
      overflow: auto;
      font-size: 0.8rem;
      background: var(--app-surface-2);
      border-radius: 6px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .meta { display: flex; flex-wrap: wrap; gap: 0.2rem 1rem; }
    .decide { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin-top: 0.6rem; }
    .note { min-width: 260px; flex: 1; }
    .empty-box {
      padding: 2rem;
      text-align: center;
      border: 1px dashed var(--app-border);
      border-radius: var(--app-radius-lg);
    }
  `,
})
export class Approvals implements OnInit {
  private readonly api = inject(ApprovalsApi);

  protected readonly filters: { value: ApprovalStatus | undefined; label: string }[] = [
    { value: 'pending', label: 'Chờ duyệt' },
    { value: undefined, label: 'Tất cả' },
  ];
  protected readonly status = signal<ApprovalStatus | undefined>('pending');
  protected readonly items = signal<Approval[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly busyId = signal<string | null>(null);
  protected notes: Record<string, string> = {};

  ngOnInit(): void {
    this.reload();
  }

  protected label(a: Approval): [string, Severity] {
    return STATUS_LABEL[a.status];
  }

  protected reload(): void {
    this.api.list({ status: this.status() }).subscribe({
      next: (list) => this.items.set(list),
      error: () => this.error.set('Không tải được danh sách duyệt.'),
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
        this.error.set(err.error?.error?.message ?? 'Không ghi được quyết định.');
        this.reload();
      },
    });
  }
}

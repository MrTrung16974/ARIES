import { DatePipe, JsonPipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { ApiService, AuditEvent, ChainStatus } from '@aiagent/core';
import { Notice, Tag } from '@aiagent/ui';

type OutcomeFilter = 'all' | 'success' | 'error';

@Component({
  selector: 'app-audit',
  imports: [
    DatePipe,
    JsonPipe,
    FormsModule,
    MatButtonModule,
    MatIcon,
    MatProgressBarModule,
    MatTableModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="hero-copy">
          <h1>Nhật ký audit</h1>
          <p class="subtitle">
            Nhật ký truy vết bất biến được liên kết bằng chuỗi băm mật mã
            (Hash-Chain) nhằm đảm bảo tính toàn vẹn dữ liệu vận hành.
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
        <button
          mat-flat-button
          class="btn-aries"
          type="button"
          [disabled]="verifying()"
          (click)="verify()"
        >
          <mat-icon svgIcon="verified" />
          {{ verifying() ? 'Đang kiểm tra…' : 'Kiểm tra toàn vẹn' }}
        </button>
      </div>
    </div>

    @if (chain(); as c) {
      @if (c.intact) {
        <ui-notice severity="success">
          Chuỗi hash nguyên vẹn ({{ c.records_checked }} bản ghi đã được xác
          minh).
        </ui-notice>
      } @else {
        <ui-notice severity="error">
          Chuỗi bị đứt tại bản ghi #{{ c.broken_at_seq }} — phát hiện dấu hiệu
          sửa/xóa dữ liệu.
        </ui-notice>
      }
    }

    <div class="filter-bar">
      <div class="sub-tabs" role="tablist" aria-label="Lọc kết quả">
        <button
          type="button"
          [class.active]="outcomeFilter() === 'all'"
          (click)="outcomeFilter.set('all')"
        >
          Tất cả · {{ events().length }}
        </button>
        <button
          type="button"
          [class.active]="outcomeFilter() === 'success'"
          (click)="outcomeFilter.set('success')"
        >
          Thành công · {{ successCount() }}
        </button>
        <button
          type="button"
          [class.active]="outcomeFilter() === 'error'"
          (click)="outcomeFilter.set('error')"
        >
          Thất bại / Từ chối · {{ errorCount() }}
        </button>
      </div>

      <div class="search-box">
        <mat-icon class="sm" svgIcon="search" aria-hidden="true" />
        <input
          type="search"
          [ngModel]="query()"
          (ngModelChange)="query.set($event)"
          placeholder="Lọc theo hành động, tài nguyên, người dùng…"
          aria-label="Lọc nhật ký audit"
        />
      </div>
    </div>

    <div class="table-wrap">
      @if (loading()) {
        <mat-progress-bar mode="indeterminate" />
      }
      <table mat-table [dataSource]="filteredEvents()">
        <ng-container matColumnDef="seq">
          <th mat-header-cell *matHeaderCellDef>#</th>
          <td mat-cell *matCellDef="let e">
            <span class="seq-badge">#{{ e.seq }}</span>
          </td>
        </ng-container>
        <ng-container matColumnDef="time">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="refresh" aria-hidden="true" />
              <span>Thời gian</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e" class="nowrap">
            {{ e.occurred_at | date: 'dd/MM/yyyy HH:mm:ss' }}
          </td>
        </ng-container>
        <ng-container matColumnDef="action">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="code" aria-hidden="true" />
              <span>Hành động</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">
            <code>{{ e.action }}</code>
          </td>
        </ng-container>
        <ng-container matColumnDef="outcome">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="check_circle" aria-hidden="true" />
              <span>Kết quả</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">
            <ui-tag
              [value]="e.outcome"
              [severity]="e.outcome === 'success' ? 'success' : 'danger'"
            />
          </td>
        </ng-container>
        <ng-container matColumnDef="actor">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="person" aria-hidden="true" />
              <span>Người thực hiện</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">{{ e.actor_id ?? '—' }}</td>
        </ng-container>
        <ng-container matColumnDef="resource">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="database" aria-hidden="true" />
              <span>Tài nguyên</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">
            {{ e.resource_type }} {{ e.resource_id }}
          </td>
        </ng-container>
        <ng-container matColumnDef="ip">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon
                class="sm"
                svgIcon="travel_explore"
                aria-hidden="true"
              />
              <span>IP</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">
            <span class="muted small">{{ e.ip ?? '—' }}</span>
          </td>
        </ng-container>
        <ng-container matColumnDef="details">
          <th mat-header-cell *matHeaderCellDef>
            <span class="th-label">
              <mat-icon class="sm" svgIcon="tune" aria-hidden="true" />
              <span>Chi tiết</span>
            </span>
          </th>
          <td mat-cell *matCellDef="let e">
            <code class="details">{{ e.details | json }}</code>
          </td>
        </ng-container>

        <tr mat-header-row *matHeaderRowDef="columns"></tr>
        <tr mat-row *matRowDef="let row; columns: columns"></tr>
        <tr class="mat-mdc-row" *matNoDataRow>
          <td class="mat-mdc-cell empty" [attr.colspan]="columns.length">
            {{
              query().trim() || outcomeFilter() !== 'all'
                ? 'Không tìm thấy bản ghi audit khớp với bộ lọc.'
                : 'Chưa có bản ghi audit nào.'
            }}
          </td>
        </tr>
      </table>
    </div>
  `,
  styles: `
    .filter-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.85rem;
      flex-wrap: wrap;
      margin-bottom: 0.9rem;
    }
    .sub-tabs {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      flex-wrap: wrap;
      button {
        border: 1px solid transparent;
        background: transparent;
        color: var(--app-muted);
        padding: 0.35rem 0.85rem;
        border-radius: 999px;
        font: inherit;
        font-size: 0.84rem;
        font-weight: 500;
        cursor: pointer;
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
        }
      }
    }
    .search-box {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      min-width: 260px;
      padding: 0.35rem 0.8rem;
      border-radius: 999px;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      color: var(--app-muted);
      input {
        flex: 1;
        border: none;
        background: transparent;
        font: inherit;
        font-size: 0.85rem;
        color: var(--app-text);
        outline: none;
      }
    }
    .table-wrap {
      overflow-x: auto;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
    }
    table {
      width: 100%;
      min-width: 62rem;
    }
    .seq-badge {
      font-family: ui-monospace, monospace;
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--app-muted);
    }
    .nowrap {
      white-space: nowrap;
    }
    .details {
      display: inline-block;
      font-size: 0.78rem;
      white-space: pre-wrap;
      word-break: break-all;
      max-width: 340px;
    }
    .empty {
      padding: 2.5rem 1rem;
      text-align: center;
      color: var(--app-muted);
    }
  `,
})
export class Audit implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly columns = [
    'seq',
    'time',
    'action',
    'outcome',
    'actor',
    'resource',
    'ip',
    'details',
  ];
  protected readonly events = signal<AuditEvent[]>([]);
  protected readonly loading = signal(false);
  protected readonly verifying = signal(false);
  protected readonly chain = signal<ChainStatus | null>(null);
  protected readonly outcomeFilter = signal<OutcomeFilter>('all');
  protected readonly query = signal('');

  protected readonly successCount = computed(
    () => this.events().filter((e) => e.outcome === 'success').length,
  );
  protected readonly errorCount = computed(
    () => this.events().filter((e) => e.outcome !== 'success').length,
  );

  protected readonly filteredEvents = computed(() => {
    const f = this.outcomeFilter();
    const q = this.query().trim().toLowerCase();
    return this.events().filter((e) => {
      if (f === 'success' && e.outcome !== 'success') return false;
      if (f === 'error' && e.outcome === 'success') return false;
      if (!q) return true;
      return (
        e.action.toLowerCase().includes(q) ||
        (e.actor_id ?? '').toLowerCase().includes(q) ||
        (e.resource_type ?? '').toLowerCase().includes(q) ||
        (e.resource_id ?? '').toLowerCase().includes(q) ||
        (e.ip ?? '').toLowerCase().includes(q)
      );
    });
  });

  ngOnInit(): void {
    this.reload();
  }

  protected reload(): void {
    this.loading.set(true);
    this.api.auditEvents(100).subscribe({
      next: (e) => this.events.set(e),
      complete: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  protected verify(): void {
    this.verifying.set(true);
    this.api.verifyAudit().subscribe({
      next: (c) => this.chain.set(c),
      complete: () => this.verifying.set(false),
      error: () => this.verifying.set(false),
    });
  }
}

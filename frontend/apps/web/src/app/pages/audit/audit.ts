import { DatePipe, JsonPipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { ApiService, AuditEvent, ChainStatus } from '@aiagent/core';
import { Notice, Tag } from '@aiagent/ui';

@Component({
  selector: 'app-audit',
  imports: [
    DatePipe,
    JsonPipe,
    MatButtonModule,
    MatIcon,
    MatProgressBarModule,
    MatTableModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="head">
      <div class="title-wrap">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div>
          <h1>Nhật ký audit</h1>
          <p class="muted subtitle">
            Nhật ký truy vết bất biến được liên kết bằng chuỗi băm mật mã
            (Hash-Chain).
          </p>
        </div>
      </div>

      <div class="actions">
        <button mat-stroked-button type="button" (click)="reload()">
          <mat-icon svgIcon="refresh" /> Tải lại
        </button>
        <button
          mat-flat-button
          class="btn-aries"
          type="button"
          [disabled]="verifying()"
          (click)="verify()"
        >
          <mat-icon svgIcon="verified" /> Kiểm tra toàn vẹn
        </button>
      </div>
    </div>

    @if (chain(); as c) {
      @if (c.intact) {
        <ui-notice severity="success">
          Chuỗi hash nguyên vẹn ({{ c.records_checked }} bản ghi).
        </ui-notice>
      } @else {
        <ui-notice severity="error">
          Chuỗi bị đứt tại bản ghi #{{ c.broken_at_seq }} — có dấu hiệu sửa/xóa
          dữ liệu.
        </ui-notice>
      }
    }

    <div class="table-wrap">
      @if (loading()) {
        <mat-progress-bar mode="indeterminate" />
      }
      <table mat-table [dataSource]="events()">
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
            Chưa có bản ghi.
          </td>
        </tr>
      </table>
    </div>
  `,
  styles: `
    .head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.25rem;
    }
    .title-wrap {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      h1 {
        margin: 0 0 0.2rem;
      }
      .subtitle {
        margin: 0;
        font-size: 0.92rem;
      }
    }
    .actions {
      display: flex;
      gap: 0.55rem;
      flex-wrap: wrap;
    }
    .table-wrap {
      overflow-x: auto;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-lg);
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
      padding: 2rem 1rem;
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

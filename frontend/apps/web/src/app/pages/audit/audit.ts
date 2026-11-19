import { DatePipe, JsonPipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { ApiService, AuditEvent, ChainStatus } from '@aiagent/core';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

@Component({
  selector: 'app-audit',
  imports: [
    TableModule,
    TagModule,
    ButtonModule,
    MessageModule,
    DatePipe,
    JsonPipe,
  ],
  template: `
    <div class="head">
      <h1>Nhật ký audit</h1>
      <div class="actions">
        <p-button
          label="Tải lại"
          icon="pi pi-refresh"
          severity="secondary"
          (onClick)="reload()"
        />
        <p-button
          label="Kiểm tra toàn vẹn"
          icon="pi pi-verified"
          [loading]="verifying()"
          (onClick)="verify()"
        />
      </div>
    </div>

    @if (chain(); as c) {
      @if (c.intact) {
        <p-message severity="success">
          Chuỗi hash nguyên vẹn ({{ c.records_checked }} bản ghi).
        </p-message>
      } @else {
        <p-message severity="error">
          Chuỗi bị đứt tại bản ghi #{{ c.broken_at_seq }} — có dấu hiệu sửa/xóa
          dữ liệu.
        </p-message>
      }
    }

    <p-table
      [value]="events()"
      [loading]="loading()"
      dataKey="seq"
      [tableStyle]="{ 'min-width': '60rem' }"
    >
      <ng-template #header>
        <tr>
          <th>#</th>
          <th>Thời gian</th>
          <th>Hành động</th>
          <th>Kết quả</th>
          <th>Người thực hiện</th>
          <th>Tài nguyên</th>
          <th>IP</th>
          <th>Chi tiết</th>
        </tr>
      </ng-template>
      <ng-template #body let-e>
        <tr>
          <td>{{ e.seq }}</td>
          <td>{{ e.occurred_at | date: 'dd/MM/yyyy HH:mm:ss' }}</td>
          <td>
            <code>{{ e.action }}</code>
          </td>
          <td>
            <p-tag
              [value]="e.outcome"
              [severity]="e.outcome === 'success' ? 'success' : 'danger'"
            />
          </td>
          <td>{{ e.actor_id ?? '—' }}</td>
          <td>{{ e.resource_type }} {{ e.resource_id }}</td>
          <td>{{ e.ip ?? '—' }}</td>
          <td>
            <code class="details">{{ e.details | json }}</code>
          </td>
        </tr>
      </ng-template>
      <ng-template #emptymessage>
        <tr>
          <td colspan="8">Chưa có bản ghi.</td>
        </tr>
      </ng-template>
    </p-table>
  `,
  styles: `
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .actions {
      display: flex;
      gap: 0.5rem;
    }
    p-message {
      display: block;
      margin-bottom: 1rem;
    }
    .details {
      font-size: 0.8rem;
      white-space: pre-wrap;
      word-break: break-all;
    }
  `,
})
export class Audit implements OnInit {
  private readonly api = inject(ApiService);

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

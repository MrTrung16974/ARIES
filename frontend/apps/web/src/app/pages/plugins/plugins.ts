import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ApiService, PluginInfo } from '@aiagent/core';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { catchError, of } from 'rxjs';

type Severity = 'success' | 'warn' | 'danger' | 'info' | 'secondary';

@Component({
  selector: 'app-plugins',
  imports: [TableModule, TagModule],
  template: `
    <h1>Plugin</h1>
    <p class="muted">
      Plugin được nạp qua entry points. Plugin lỗi bị vô hiệu, không làm sập hệ
      thống.
    </p>
    <p-table
      [value]="plugins() ?? []"
      dataKey="entry_point"
      [tableStyle]="{ 'min-width': '50rem' }"
    >
      <ng-template #header>
        <tr>
          <th>ID</th>
          <th>Loại</th>
          <th>Phiên bản</th>
          <th>Trạng thái</th>
          <th>Tool</th>
          <th>Ghi chú</th>
        </tr>
      </ng-template>
      <ng-template #body let-p>
        <tr>
          <td>
            <strong>{{ p.name ?? p.entry_point }}</strong>
            <div class="muted">{{ p.id }}</div>
          </td>
          <td>{{ p.kind ?? '—' }}</td>
          <td>{{ p.version ?? '—' }}</td>
          <td><p-tag [value]="p.status" [severity]="statusSeverity(p)" /></td>
          <td>
            @for (t of p.tools; track t.name) {
              <div>
                <code>{{ t.name }}</code>
                <p-tag [value]="t.risk" [severity]="riskSeverity(t.risk)" />
              </div>
            }
          </td>
          <td>{{ p.reason }}</td>
        </tr>
      </ng-template>
      <ng-template #emptymessage>
        <tr>
          <td colspan="6">Không có plugin nào.</td>
        </tr>
      </ng-template>
    </p-table>
  `,
  styles: `
    .muted {
      color: var(--p-text-muted-color);
    }
    td div {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      margin-bottom: 0.25rem;
    }
  `,
})
export class Plugins {
  protected readonly plugins = toSignal(
    inject(ApiService)
      .plugins()
      .pipe(catchError(() => of<PluginInfo[]>([]))),
  );

  protected statusSeverity(p: PluginInfo): Severity {
    return p.status === 'active'
      ? 'success'
      : p.status === 'disabled'
        ? 'warn'
        : 'danger';
  }

  protected riskSeverity(risk: string): Severity {
    switch (risk) {
      case 'read':
        return 'info';
      case 'write':
        return 'secondary';
      case 'external_send':
        return 'warn';
      default:
        return 'danger';
    }
  }
}

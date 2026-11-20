import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatTableModule } from '@angular/material/table';
import { ApiService, PluginInfo } from '@aiagent/core';
import { Severity, Tag } from '@aiagent/ui';
import { catchError, of } from 'rxjs';

@Component({
  selector: 'app-plugins',
  imports: [MatTableModule, Tag],
  template: `
    <h1>Plugin</h1>
    <p class="muted">
      Plugin được nạp qua entry points. Plugin lỗi bị vô hiệu, không làm sập hệ
      thống.
    </p>
    <div class="table-wrap">
      <table mat-table [dataSource]="plugins() ?? []">
        <ng-container matColumnDef="id">
          <th mat-header-cell *matHeaderCellDef>ID</th>
          <td mat-cell *matCellDef="let p">
            <strong>{{ p.name ?? p.entry_point }}</strong>
            <div class="muted">{{ p.id }}</div>
          </td>
        </ng-container>
        <ng-container matColumnDef="kind">
          <th mat-header-cell *matHeaderCellDef>Loại</th>
          <td mat-cell *matCellDef="let p">{{ p.kind ?? '—' }}</td>
        </ng-container>
        <ng-container matColumnDef="version">
          <th mat-header-cell *matHeaderCellDef>Phiên bản</th>
          <td mat-cell *matCellDef="let p">{{ p.version ?? '—' }}</td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Trạng thái</th>
          <td mat-cell *matCellDef="let p">
            <ui-tag [value]="p.status" [severity]="statusSeverity(p)" />
          </td>
        </ng-container>
        <ng-container matColumnDef="tools">
          <th mat-header-cell *matHeaderCellDef>Tool</th>
          <td mat-cell *matCellDef="let p">
            @for (t of p.tools; track t.name) {
              <div class="tool">
                <code>{{ t.name }}</code>
                <ui-tag [value]="t.risk" [severity]="riskSeverity(t.risk)" />
              </div>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="reason">
          <th mat-header-cell *matHeaderCellDef>Ghi chú</th>
          <td mat-cell *matCellDef="let p">{{ p.reason }}</td>
        </ng-container>

        <tr mat-header-row *matHeaderRowDef="columns"></tr>
        <tr mat-row *matRowDef="let row; columns: columns"></tr>
        <tr class="mat-mdc-row" *matNoDataRow>
          <td class="mat-mdc-cell empty" [attr.colspan]="columns.length">
            Không có plugin nào.
          </td>
        </tr>
      </table>
    </div>
  `,
  styles: `
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-lg);
    }
    table {
      min-width: 50rem;
    }
    .tool {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      margin: 0.25rem 0;
    }
    .empty {
      padding: 1rem;
    }
  `,
})
export class Plugins {
  protected readonly columns = [
    'id',
    'kind',
    'version',
    'status',
    'tools',
    'reason',
  ];
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

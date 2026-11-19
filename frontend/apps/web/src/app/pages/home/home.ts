import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ApiService, SessionService } from '@aiagent/core';
import { CardModule } from 'primeng/card';
import { TagModule } from 'primeng/tag';
import { catchError, of } from 'rxjs';

@Component({
  selector: 'app-home',
  imports: [CardModule, TagModule, DatePipe],
  template: `
    <h1>
      Xin chào{{ session.displayName() ? ', ' + session.displayName() : '' }}
    </h1>
    <div class="grid">
      <p-card header="Tài khoản">
        @if (session.me(); as me) {
          <dl>
            <dt>Tenant</dt>
            <dd>{{ me.tenant_id }}</dd>
            <dt>Email</dt>
            <dd>{{ me.email ?? '—' }}</dd>
            <dt>Vai trò</dt>
            <dd>
              @for (role of me.roles; track role) {
                <p-tag [value]="role" />
              } @empty {
                —
              }
            </dd>
            <dt>Lần đầu đăng nhập</dt>
            <dd>{{ me.created_at | date: 'dd/MM/yyyy HH:mm' }}</dd>
          </dl>
        } @else {
          <p>Đang tải…</p>
        }
      </p-card>
      <p-card header="Hệ thống">
        @if (info(); as i) {
          <dl>
            <dt>Ứng dụng</dt>
            <dd>{{ i.name }}</dd>
            <dt>Phiên bản</dt>
            <dd>{{ i.version }}</dd>
            <dt>Môi trường</dt>
            <dd><p-tag [value]="i.env" severity="info" /></dd>
          </dl>
        } @else {
          <p>Không kết nối được API.</p>
        }
      </p-card>
      <p-card header="Lộ trình">
        <p>
          Phase 1 — runtime: tạo agent bằng form, chat stream, tool, lịch sử hội
          thoại.
        </p>
        <p>Tiếp theo: Agent Architect — tạo agent bằng mô tả (Phase 2).</p>
      </p-card>
    </div>
  `,
  styles: `
    .grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }
    dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 0.5rem 1rem;
      margin: 0;
    }
    dt {
      color: var(--p-text-muted-color);
    }
    dd {
      margin: 0;
      display: flex;
      gap: 0.25rem;
      flex-wrap: wrap;
    }
  `,
})
export class Home {
  protected readonly session = inject(SessionService);
  protected readonly info = toSignal(
    inject(ApiService)
      .systemInfo()
      .pipe(catchError(() => of(null))),
  );
}

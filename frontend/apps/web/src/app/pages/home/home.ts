import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { ApiService, SessionService } from '@aiagent/core';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';
import { catchError, of } from 'rxjs';

@Component({
  selector: 'app-home',
  imports: [
    ButtonModule,
    CardModule,
    FormsModule,
    TagModule,
    TextareaModule,
    DatePipe,
  ],
  template: `
    <h1>
      Xin chào{{ session.displayName() ? ', ' + session.displayName() : '' }}
    </h1>
    @if (session.hasAnyRole('builder', 'admin')) {
      <section class="ask">
        <label for="ask">Bạn muốn tạo agent gì?</label>
        <div class="ask-row">
          <textarea
            pTextarea
            id="ask"
            rows="2"
            [(ngModel)]="idea"
            (keydown.enter)="$event.preventDefault(); create()"
            placeholder="Ví dụ: Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị"
          ></textarea>
          <p-button
            label="Tạo"
            icon="pi pi-bolt"
            [disabled]="!idea.trim()"
            (onClick)="create()"
          />
        </div>
        <p class="muted">
          Agent Architect sẽ hỏi lại nếu cần, dựng bản nháp và tự chạy thử. Mẹo:
          Ctrl+K để mở command bar.
        </p>
      </section>
    }
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
          Phase 2 — Agent Architect: tạo và sửa agent bằng mô tả, eval tự động,
          command bar.
        </p>
        <p>
          Tiếp theo: Skill, Policy, Connector (Postgres, web search, duyệt thao
          tác rủi ro) — Phase 3.
        </p>
      </p-card>
    </div>
  `,
  styles: `
    .ask {
      margin-bottom: 1.5rem;
      display: grid;
      gap: 0.5rem;
      label {
        font-weight: 600;
      }
    }
    .ask-row {
      display: flex;
      gap: 0.5rem;
      align-items: flex-end;
      textarea {
        flex: 1;
      }
    }
    .muted {
      color: var(--p-text-muted-color);
      margin: 0;
      font-size: 0.85rem;
    }
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
  private readonly router = inject(Router);
  protected idea = '';

  protected create(): void {
    const q = this.idea.trim();
    if (q) void this.router.navigate(['/builder'], { queryParams: { q } });
  }
  protected readonly info = toSignal(
    inject(ApiService)
      .systemInfo()
      .pipe(catchError(() => of(null))),
  );
}

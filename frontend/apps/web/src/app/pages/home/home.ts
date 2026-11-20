import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { ApiService, SessionService } from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Tag } from '@aiagent/ui';
import { catchError, of } from 'rxjs';

@Component({
  selector: 'app-home',
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    Tag,
  ],
  template: `
    <section class="hero">
      <img
        src="assets/brand/aries-logo.webp"
        alt="Logo ARIES"
        width="160"
        height="136"
      />
      <div>
        <p class="eyebrow">ARIES</p>
        <h1>
          Xin chào{{
            session.displayName() ? ', ' + session.displayName() : ''
          }}
        </h1>
        <p class="tagline">
          Mô tả agent bằng lời — ARIES dựng, chạy thử và vận hành cho bạn.
        </p>
      </div>
    </section>
    @if (session.hasAnyRole('builder', 'admin')) {
      <section class="ask">
        <label for="ask">Bạn muốn tạo agent gì?</label>
        <div class="ask-row">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <textarea
              matInput
              id="ask"
              rows="2"
              [(ngModel)]="idea"
              (keydown.enter)="$event.preventDefault(); create()"
              placeholder="Ví dụ: Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị"
            ></textarea>
          </mat-form-field>
          <button
            mat-flat-button
            type="button"
            [disabled]="!idea.trim()"
            (click)="create()"
          >
            <mat-icon svgIcon="bolt" /> Tạo
          </button>
        </div>
        <p class="muted">
          Agent Architect sẽ hỏi lại nếu cần, dựng bản nháp và tự chạy thử. Mẹo:
          Ctrl+K để mở command bar.
        </p>
      </section>
    }
    <div class="grid">
      <mat-card appearance="outlined"
        ><mat-card-header
          ><mat-card-title>Tài khoản</mat-card-title></mat-card-header
        ><mat-card-content>
          @if (session.me(); as me) {
            <dl>
              <dt>Tenant</dt>
              <dd>{{ me.tenant_id }}</dd>
              <dt>Email</dt>
              <dd>{{ me.email ?? '—' }}</dd>
              <dt>Vai trò</dt>
              <dd>
                @for (role of me.roles; track role) {
                  <ui-tag [value]="role" />
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
        </mat-card-content></mat-card
      >
      <mat-card appearance="outlined"
        ><mat-card-header
          ><mat-card-title>Hệ thống</mat-card-title></mat-card-header
        ><mat-card-content>
          @if (info(); as i) {
            <dl>
              <dt>Ứng dụng</dt>
              <dd>{{ i.name }}</dd>
              <dt>Phiên bản</dt>
              <dd>{{ i.version }}</dd>
              <dt>Môi trường</dt>
              <dd><ui-tag [value]="i.env" severity="info" /></dd>
            </dl>
          } @else {
            <p>Không kết nối được API.</p>
          }
        </mat-card-content></mat-card
      >
      <mat-card appearance="outlined"
        ><mat-card-header
          ><mat-card-title>Lộ trình</mat-card-title></mat-card-header
        ><mat-card-content>
          <p>
            Phase 2 — Agent Architect: tạo và sửa agent bằng mô tả, eval tự
            động, command bar.
          </p>
          <p>
            Tiếp theo: Skill, Policy, Connector (Postgres, web search, duyệt
            thao tác rủi ro) — Phase 3.
          </p>
        </mat-card-content></mat-card
      >
    </div>
  `,
  styles: `
    .hero {
      display: flex;
      align-items: center;
      gap: 1.25rem;
      padding: 1rem 1.25rem;
      margin-bottom: 1.5rem;
      border-radius: var(--app-radius-xl);
      background: radial-gradient(140% 180% at 0% 50%, #2a0a24 0%, #0b0614 60%);
      color: #f5e9f1;
      img {
        border-radius: 12px;
        filter: drop-shadow(0 0 18px rgba(255, 61, 139, 0.45));
      }
      h1 {
        margin: 0.15rem 0 0.35rem;
      }
    }
    .eyebrow {
      margin: 0;
      letter-spacing: 0.3em;
      font-weight: 700;
      color: #ff7ab0;
      text-shadow: 0 0 10px rgba(255, 61, 139, 0.7);
    }
    .tagline {
      margin: 0;
      color: rgba(245, 233, 241, 0.75);
    }
    @media (max-width: 600px) {
      .hero img {
        width: 96px;
        height: auto;
      }
    }
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
      mat-form-field {
        flex: 1;
      }
      button {
        margin-bottom: 0.25rem;
      }
    }
    .muted {
      color: var(--app-muted);
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
      color: var(--app-muted);
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

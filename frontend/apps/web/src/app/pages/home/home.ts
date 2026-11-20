import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { ApiService, ApprovalsApi, SessionService } from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Tag } from '@aiagent/ui';
import { catchError, of } from 'rxjs';

const IDEA_PRESETS = [
  'Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị',
  'Trợ lý CSKH giải đáp chính sách bảo hành & đổi trả, lịch sự, không tự ý hứa hoàn tiền',
  'Trợ lý IT Helpdesk hướng dẫn xử lý sự cố mạng và tài khoản nội bộ theo từng bước',
];

@Component({
  selector: 'app-home',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    Tag,
  ],
  template: `
    <div class="greet-head">
      <h1>
        Xin chào{{
          session.displayName() ? ', ' + session.displayName() : ''
        }}
      </h1>
      <p class="muted">
        Nền tảng AI Agent nội bộ của bạn đang hoạt động và sẵn sàng hỗ trợ.
      </p>
    </div>

    <div class="flow-status-card">
      <div class="status-left">
        <div class="logo-halo">
          <img
            src="assets/brand/aries-logo-v3.webp?v=aries4"
            alt="Logo ARIES"
            width="52"
            height="52"
          />
        </div>
        <div>
          <div class="status-title-row">
            <strong>ARIES AI Platform</strong>
            <span class="run-pill">● Running</span>
          </div>
          <div class="muted small">
            @if (info(); as i) {
              {{ i.name }} · v{{ i.version }} · {{ i.env }} · on-premise
            } @else {
              claude-opus-5-5 · LangGraph Runtime · on-premise
            }
          </div>
        </div>
      </div>

      <div class="status-actions">
        <button
          mat-flat-button
          class="btn-aries"
          type="button"
          (click)="askAries()"
          title="Nói việc cần làm — ARIES Master sẽ giao cho agent phù hợp"
        >
          <mat-icon svgIcon="forum" /> Hỏi ARIES
        </button>
        <a mat-stroked-button class="pill-btn" routerLink="/agents">
          <mat-icon svgIcon="robot_2" /> Danh sách Agent
        </a>
        @if (session.hasAnyRole('builder', 'admin')) {
          <a mat-flat-button class="btn-aries" routerLink="/builder">
            <mat-icon svgIcon="bolt" /> Mở Architect
          </a>
        }
      </div>
    </div>

    @if (session.hasAnyRole('builder', 'admin')) {
      <section class="ask-card">
        <div class="ask-title">
          <span class="icon-badge accent">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
          </span>
          <div>
            <label for="ask">Bạn muốn tạo AI Agent gì hôm nay?</label>
            <p class="muted small">
              Mô tả agent bằng lời — ARIES dựng, chạy thử (Eval) và vận hành cho
              bạn. Mẹo: Ctrl+K để mở command bar.
            </p>
          </div>
        </div>

        <div class="ask-row">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <textarea
              matInput
              id="ask"
              rows="2"
              [(ngModel)]="idea"
              (keydown.enter)="$event.preventDefault(); create()"
              placeholder="Ví dụ: Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị…"
            ></textarea>
          </mat-form-field>
          <button
            mat-flat-button
            class="btn-aries create-btn"
            type="button"
            [disabled]="!idea.trim()"
            (click)="create()"
          >
            <mat-icon svgIcon="bolt" /> Tạo Agent
          </button>
        </div>

        <div class="presets">
          <span class="presets-label">
            <mat-icon class="sm" svgIcon="play_arrow" aria-hidden="true" />
            Thử mẫu nhanh:
          </span>
          @for (p of presets; track p) {
            <button type="button" class="preset-chip" (click)="usePreset(p)">
              <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
              <span>{{ p }}</span>
            </button>
          }
        </div>
      </section>
    }

    <div class="flow-info-strip">
      ARIES kết hợp LangGraph Runtime, bộ kiểm thử Eval độc lập và cơ chế cô lập
      Plugin/Skill theo tiêu chuẩn bảo mật doanh nghiệp.
    </div>

    <div class="cards-grid">
      <article class="flow-card">
        <div class="card-top">
          <span class="icon-badge">
            <mat-icon class="sm" svgIcon="person" aria-hidden="true" />
          </span>
          <div class="card-body">
            <div class="title-line">
              <strong>Tài khoản &amp; Định danh</strong>
              <ui-tag value="OIDC" severity="primary" />
              <ui-tag value="Enabled" severity="success" />
            </div>
            <p class="desc">
              Phiên làm việc được xác thực tập trung qua Keycloak với phân quyền
              RBAC và cô lập dữ liệu theo Tenant.
            </p>
          </div>
        </div>

        @if (session.me(); as me) {
          <div class="chips-area">
            <span class="flow-chip chip-read">
              <mat-icon class="sm" svgIcon="dns" aria-hidden="true" />
              tenant: {{ me.tenant_id }}
            </span>
            <span class="flow-chip chip-write">
              <mat-icon class="sm" svgIcon="forum" aria-hidden="true" />
              {{ me.email ?? 'no-email' }}
            </span>
            @for (role of me.roles; track role) {
              <span class="flow-chip chip-warn">
                <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
                role: {{ role }}
              </span>
            }
          </div>
          <div class="card-foot">
            <span class="ver"
              >Đăng nhập: {{ me.created_at | date: 'dd/MM/yyyy HH:mm' }}</span
            >
            <span class="state-pill is-active">Đang hoạt động</span>
          </div>
        } @else {
          <p class="muted small">Đang tải thông tin tài khoản…</p>
        }
      </article>

      <article class="flow-card">
        <div class="card-top">
          <span class="icon-badge">
            <mat-icon class="sm" svgIcon="dns" aria-hidden="true" />
          </span>
          <div class="card-body">
            <div class="title-line">
              <strong>Hệ thống &amp; Lộ trình</strong>
              @if (info(); as i) {
                <ui-tag [value]="i.env" severity="info" />
              }
              <ui-tag value="Phase 2–3" severity="success" />
            </div>
            <p class="desc">
              Phase 2 (Agent Architect, Eval tự động, Command Bar) và Phase 3
              (Skill, Policy, Connector Postgres &amp; Web Search, duyệt HITL).
            </p>
          </div>
        </div>

        <div class="chips-area">
          <span class="flow-chip chip-read">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
            architect.eval
          </span>
          <span class="flow-chip chip-read">
            <mat-icon class="sm" svgIcon="database" aria-hidden="true" />
            connector.postgres
          </span>
          <span class="flow-chip chip-write">
            <mat-icon class="sm" svgIcon="travel_explore" aria-hidden="true" />
            skill.web
          </span>
          <span class="flow-chip chip-warn">
            <mat-icon class="sm" svgIcon="gavel" aria-hidden="true" />
            policy.hitl
          </span>
        </div>

        <div class="card-foot">
          <span class="ver"
            >v{{ info()?.version ?? '0.1.0' }} ·
            {{ info()?.name ?? 'ARIES' }}</span
          >
          <a routerLink="/plugins" class="state-pill is-active link-pill">
            Xem Plugin
          </a>
        </div>
      </article>
    </div>
  `,
  styles: `
    .greet-head {
      margin-bottom: 1.1rem;
      h1 {
        font-size: 1.55rem;
        margin: 0 0 0.15rem;
      }
      p {
        margin: 0;
        font-size: 0.9rem;
      }
    }
    .status-left {
      display: flex;
      align-items: center;
      gap: 0.95rem;
    }
    .logo-halo {
      display: inline-flex;
      border-radius: 50%;
      box-shadow:
        0 8px 20px rgba(0, 0, 0, 0.35),
        0 0 20px rgba(255, 61, 139, 0.4);
      img {
        display: block;
        border-radius: 50%;
      }
    }
    .status-title-row {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      font-size: 1.04rem;
      margin-bottom: 0.15rem;
    }
    .run-pill {
      display: inline-flex;
      align-items: center;
      padding: 0.1rem 0.55rem;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 600;
      background: var(--app-success-bg);
      color: var(--app-success);
      border: 1px solid var(--app-success-border);
    }
    .status-actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .pill-btn {
      border-radius: 999px !important;
    }
    .ask-card {
      margin-bottom: 1.15rem;
      padding: 1.15rem 1.25rem;
      border-radius: var(--app-radius-xl);
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      box-shadow: var(--app-shadow-sm);
      display: grid;
      gap: 0.85rem;
    }
    .ask-title {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      label {
        font-weight: 700;
        display: block;
      }
      p {
        margin: 0.1rem 0 0;
      }
    }
    .ask-row {
      display: flex;
      gap: 0.6rem;
      align-items: flex-end;
      flex-wrap: wrap;
      mat-form-field {
        flex: 1;
        min-width: 240px;
      }
      .create-btn {
        min-height: 44px;
      }
    }
    .presets {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.4rem;
    }
    .presets-label {
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
      font-size: 0.76rem;
      font-weight: 600;
      color: var(--app-muted);
    }
    .preset-chip {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      border: 1px solid var(--app-border);
      background: var(--app-surface-2);
      color: var(--app-text);
      border-radius: 999px;
      padding: 0.25rem 0.75rem;
      font: inherit;
      font-size: 0.76rem;
      cursor: pointer;
      max-width: 100%;
      mat-icon {
        color: var(--app-primary);
      }
      span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      &:hover {
        border-color: var(--app-primary);
        background: var(--app-highlight);
      }
    }
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 1.1rem;
    }
    .flow-card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1.2rem 1.25rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 0.95rem;
    }
    .card-top {
      display: flex;
      align-items: flex-start;
      gap: 0.85rem;
    }
    .card-body {
      flex: 1;
      min-width: 0;
    }
    .title-line {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
      margin-bottom: 0.35rem;
    }
    .desc {
      margin: 0;
      font-size: 0.85rem;
      color: var(--app-muted);
      line-height: 1.48;
    }
    .chips-area {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }
    .card-foot {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-top: 0.65rem;
      border-top: 1px solid var(--app-border);
      .ver {
        font-family: ui-monospace, monospace;
        font-size: 0.78rem;
        color: var(--app-muted);
      }
    }
    .state-pill {
      display: inline-flex;
      align-items: center;
      padding: 0.25rem 0.75rem;
      border-radius: 999px;
      font-size: 0.76rem;
      font-weight: 600;
      text-decoration: none;
      &.is-active {
        background: var(--app-highlight);
        color: var(--app-primary);
        border: 1px solid rgba(255, 61, 139, 0.28);
      }
    }
  `,
})
export class Home {
  protected readonly session = inject(SessionService);
  private readonly router = inject(Router);
  protected readonly presets = IDEA_PRESETS;
  protected idea = '';

  protected usePreset(preset: string): void {
    this.idea = preset;
  }

  private readonly approvals = inject(ApprovalsApi);

  /** Mở hội thoại với ARIES Master (tự tạo lần đầu). */
  protected askAries(): void {
    this.approvals.master().subscribe({
      next: (m) => void this.router.navigate(['/agents', m.agent_id, 'chat']),
    });
  }

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

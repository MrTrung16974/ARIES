import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  AgentsApi,
  AgentSummary,
  ApiService,
  ApprovalsApi,
  SessionService,
} from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Tag } from '@aiagent/ui';
import { catchError, of } from 'rxjs';

type AgentCategory = 'all' | 'worker' | 'draft';

const MASTER_TASK_PRESETS = [
  'Kiểm tra giờ hệ thống hiện tại và báo cáo tóm tắt các agent chuyên trách đang sẵn sàng',
  'Tra cứu danh sách các bảng dữ liệu nội bộ và phân tích tổng quan',
  'Phân tích yêu cầu hỗ trợ khách hàng và giao việc cho agent chuyên trách phù hợp',
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
        Giao việc trực tiếp cho ARIES Master hoặc chọn các Agent chuyên trách
        bên dưới theo từng phân loại.
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
              {{ i.name }} · v{{ i.version }} · {{ i.env }} ·
              {{ agents().length }} agent
            } @else {
              ARIES Multi-Agent Orchestrator · on-premise
            }
          </div>
        </div>
      </div>

      <div class="status-actions">
        <button
          mat-stroked-button
          class="pill-btn"
          type="button"
          (click)="openMasterChat()"
        >
          <mat-icon svgIcon="forum" /> Phòng Chat Master
        </button>
        <a mat-stroked-button class="pill-btn" routerLink="/agents">
          <mat-icon svgIcon="add" /> Tạo &amp; Quản lý Agent
        </a>
      </div>
    </div>

    <!-- Thanh Chat Giao Việc của ARIES Master -->
    <section class="master-chat-card">
      <div class="master-head">
        <div class="master-title">
          <span class="icon-badge accent">
            <mat-icon class="sm" svgIcon="forum" aria-hidden="true" />
          </span>
          <div>
            <div class="title-with-tag">
              <label for="master-task"
                >ARIES Master — Thanh Chat Giao Việc &amp; Điều Phối</label
              >
              <ui-tag value="Master Agent" severity="primary" />
              <ui-tag value="Auto-Delegate" severity="success" />
            </div>
            <p class="muted small">
              Nhập công việc cần xử lý — ARIES Master sẽ tự động phân tích và
              giao việc cho các Agent chuyên trách bên dưới.
            </p>
          </div>
        </div>
      </div>

      <div class="composer-box">
        <textarea
          id="master-task"
          rows="2"
          [(ngModel)]="taskText"
          (keydown.enter)="$event.preventDefault(); delegateToMaster()"
          placeholder="Nhập yêu cầu giao việc cho ARIES Master… (ví dụ: Tra cứu giờ làm việc và kiểm tra dữ liệu hệ thống)"
        ></textarea>
        <div class="composer-bar">
          <div class="composer-hints">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
            <span
              >Nhấn <kbd>Enter</kbd> để giao việc ngay · Master tự điều phối
              Agent con</span
            >
          </div>
          <button
            mat-flat-button
            class="btn-aries"
            type="button"
            [disabled]="sendingMaster()"
            (click)="delegateToMaster()"
          >
            <mat-icon svgIcon="send" />
            {{ taskText.trim() ? 'Giao việc cho Master' : 'Mở Chat Master' }}
          </button>
        </div>
      </div>

      <div class="presets">
        <span class="presets-label">
          <mat-icon class="sm" svgIcon="play_arrow" aria-hidden="true" />
          Giao việc mẫu:
        </span>
        @for (p of taskPresets; track p) {
          <button type="button" class="preset-chip" (click)="useTaskPreset(p)">
            <mat-icon class="sm" svgIcon="forum" aria-hidden="true" />
            <span>{{ p }}</span>
          </button>
        }
      </div>
    </section>

    <!-- Danh sách các Agent theo từng loại -->
    <div class="agents-section-head">
      <div>
        <h2>Hệ thống Agent theo phân loại</h2>
        <p class="muted small">
          Danh sách các Agent điều phối, Agent chuyên trách đang vận hành và
          Agent bản nháp trong tổ chức.
        </p>
      </div>

      <div class="search-box">
        <mat-icon class="sm" svgIcon="search" aria-hidden="true" />
        <input
          type="search"
          [ngModel]="query()"
          (ngModelChange)="query.set($event)"
          placeholder="Tìm nhanh agent…"
          aria-label="Tìm nhanh agent"
        />
      </div>
    </div>

    <div class="sub-tabs" role="tablist" aria-label="Phân loại Agent">
      <button
        type="button"
        [class.active]="category() === 'all'"
        (click)="category.set('all')"
      >
        <mat-icon class="sm" svgIcon="dashboard" aria-hidden="true" />
        Tất cả · {{ agents().length }}
      </button>
      <button
        type="button"
        [class.active]="category() === 'worker'"
        (click)="category.set('worker')"
      >
        <mat-icon class="sm" svgIcon="check_circle" aria-hidden="true" />
        Chuyên trách (Đang chạy) · {{ workerCount() }}
      </button>
      <button
        type="button"
        [class.active]="category() === 'draft'"
        (click)="category.set('draft')"
      >
        <mat-icon class="sm" svgIcon="edit" aria-hidden="true" />
        Bản nháp · {{ draftCount() }}
      </button>
    </div>

    <div class="flow-info-strip">
      <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
      <span>
        ARIES Master có thể ủy quyền (delegate) cho các Agent thuộc nhóm
        <strong>Chuyên trách (Đang chạy)</strong>. Để khởi tạo Agent mới, chuyển
        sang tab <a routerLink="/agents">Agent</a>.
      </span>
    </div>

    @if (loadingAgents()) {
      <p class="muted">Đang tải danh sách agent…</p>
    } @else if (filteredAgents().length === 0) {
      <div class="empty-state-card">
        <div class="empty-icon-ring" aria-hidden="true">
          <mat-icon svgIcon="robot_2" />
        </div>
        <h2>Chưa có agent nào trong phân loại này</h2>
        <p>
          {{
            query().trim()
              ? 'Không tìm thấy agent phù hợp với từ khóa tìm kiếm.'
              : 'Khởi tạo AI Agent chuyên trách mới để ARIES Master có thể điều phối công việc tự động.'
          }}
        </p>
        <div class="empty-actions">
          @if (query().trim() || category() !== 'all') {
            <button
              mat-stroked-button
              class="pill-btn"
              type="button"
              (click)="query.set(''); category.set('all')"
            >
              <mat-icon svgIcon="refresh" /> Xem tất cả
            </button>
          }
          <a mat-flat-button class="btn-aries" routerLink="/agents">
            <mat-icon svgIcon="add" /> Đi tới trang Tạo Agent
          </a>
        </div>
      </div>
    } @else {
      <div class="cards-grid">
        @for (a of filteredAgents(); track a.id) {
          <article class="flow-card">
            <div class="card-top">
              <span class="icon-badge" aria-hidden="true">
                <mat-icon class="sm" svgIcon="robot_2" />
              </span>
              <div class="card-body">
                <div class="title-line">
                  <strong>{{ a.name }}</strong>
                  <ui-tag [value]="categoryLabel(a)" severity="warn" />
                  <ui-tag
                    [value]="a.published_version ? 'Enabled' : 'Nháp'"
                    [severity]="a.published_version ? 'success' : 'secondary'"
                  />
                </div>
                <p class="desc">
                  {{ a.description || 'Agent chuyên trách thực thi tác vụ nội bộ.' }}
                </p>
              </div>
            </div>

            <div class="chips-area">
              <span class="flow-chip chip-read">
                <mat-icon class="sm" svgIcon="code" aria-hidden="true" />
                version: v{{ a.latest_version }}
              </span>
              @if (a.published_version) {
                <span class="flow-chip chip-write">
                  <mat-icon
                    class="sm"
                    svgIcon="check_circle"
                    aria-hidden="true"
                  />
                  live: v{{ a.published_version }}
                </span>
              }
              <span class="flow-chip chip-warn">
                <mat-icon class="sm" svgIcon="refresh" aria-hidden="true" />
                {{ a.updated_at | date: 'dd/MM HH:mm' }}
              </span>
            </div>

            <div class="card-foot">
              <span class="ver">v{{ a.latest_version }}.0</span>
              <div class="foot-actions">
                @if (a.can_edit) {
                  <a
                    mat-stroked-button
                    class="pill-btn"
                    [routerLink]="['/agents', a.id, 'edit']"
                  >
                    <mat-icon svgIcon="edit" /> Cấu hình
                  </a>
                }
                <a
                  mat-flat-button
                  class="btn-aries"
                  [routerLink]="['/agents', a.id, 'chat']"
                  [queryParams]="a.published_version ? {} : { draft: 1 }"
                >
                  <mat-icon svgIcon="forum" />
                  Chat
                </a>
              </div>
            </div>
          </article>
        }
      </div>
    }
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
    .status-actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .master-chat-card {
      margin-bottom: 1.4rem;
      padding: 1.2rem 1.3rem;
      border-radius: var(--app-radius-xl);
      background: var(--app-surface);
      border: 1px solid var(--app-border-strong);
      box-shadow: var(--app-shadow-md);
      display: grid;
      gap: 0.85rem;
    }
    .master-title {
      display: flex;
      align-items: flex-start;
      gap: 0.8rem;
      label {
        font-weight: 700;
        font-size: 1.02rem;
        cursor: pointer;
      }
      p {
        margin: 0.15rem 0 0;
      }
    }
    .title-with-tag {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .master-input-row {
      display: flex;
      gap: 0.6rem;
      align-items: flex-end;
      flex-wrap: wrap;
      mat-form-field {
        flex: 1;
        min-width: 240px;
      }
      .send-task-btn {
        min-height: 44px;
      }
    }
    .agents-section-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 1rem;
      flex-wrap: wrap;
      margin-bottom: 0.75rem;
      h2 {
        margin: 0 0 0.15rem;
      }
      p {
        margin: 0;
      }
    }
    .search-box {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      min-width: 220px;
      padding: 0.32rem 0.75rem;
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
    .sub-tabs {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      flex-wrap: wrap;
      margin-bottom: 0.85rem;
      button {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        border: 1px solid transparent;
        background: transparent;
        color: var(--app-muted);
        padding: 0.35rem 0.85rem;
        border-radius: 999px;
        font: inherit;
        font-size: 0.84rem;
        font-weight: 500;
        cursor: pointer;
        &.active {
          background: var(--app-surface);
          color: var(--app-text);
          font-weight: 600;
          border-color: var(--app-border);
          box-shadow: var(--app-shadow-sm);
          mat-icon {
            color: var(--app-primary);
          }
        }
      }
    }
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
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
      transition: all 0.18s ease;
      &:hover {
        border-color: var(--app-border-strong);
        box-shadow: var(--app-shadow-md);
        transform: translateY(-2px);
      }
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
    .foot-actions {
      display: flex;
      gap: 0.45rem;
    }
    .empty-box {
      padding: 2rem;
      text-align: center;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
    }
  `,
})
export class Home implements OnInit {
  protected readonly session = inject(SessionService);
  private readonly router = inject(Router);
  private readonly approvals = inject(ApprovalsApi);
  private readonly agentsApi = inject(AgentsApi);

  protected readonly taskPresets = MASTER_TASK_PRESETS;
  protected taskText = '';
  protected readonly sendingMaster = signal(false);

  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly masterAgentId = signal<string | null>(null);
  protected readonly loadingAgents = signal(true);
  protected readonly category = signal<AgentCategory>('all');
  protected readonly query = signal('');

  protected readonly info = toSignal(
    inject(ApiService)
      .systemInfo()
      .pipe(catchError(() => of(null))),
  );

  protected readonly workerCount = computed(
    () =>
      this.agents().filter((a) => !!a.published_version).length,
  );
  protected readonly draftCount = computed(
    () =>
      this.agents().filter((a) => !a.published_version).length,
  );

  protected readonly filteredAgents = computed(() => {
    const q = this.query().trim().toLowerCase();
    const cat = this.category();
    return this.agents().filter((a) => {
      if (cat === 'worker' && !a.published_version) return false;
      if (cat === 'draft' && !!a.published_version) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description ?? '').toLowerCase().includes(q)
      );
    });
  });

  ngOnInit(): void {
    this.approvals
      .master()
      .pipe(catchError(() => of(null)))
      .subscribe((m) => {
        if (m) this.masterAgentId.set(m.agent_id);
        this.loadAgents();
      });
  }

  protected isMasterAgent(a: AgentSummary): boolean {
    return (
      a.id === this.masterAgentId() ||
      a.name.toLowerCase().includes('aries master')
    );
  }

  protected categoryLabel(a: AgentSummary): string {
    return a.published_version ? 'Chuyên trách' : 'Đang phát triển';
  }

  protected useTaskPreset(preset: string): void {
    this.taskText = preset;
  }

  protected openMasterChat(): void {
    this.navigateToMaster();
  }

  protected delegateToMaster(): void {
    const q = this.taskText.trim();
    this.navigateToMaster(q || undefined);
  }

  private navigateToMaster(q?: string): void {
    const existingId = this.masterAgentId();
    if (existingId) {
      void this.router.navigate(['/agents', existingId, 'chat'], {
        queryParams: q ? { q } : {},
      });
      return;
    }
    this.sendingMaster.set(true);
    this.approvals.master().subscribe({
      next: (m) => {
        this.sendingMaster.set(false);
        this.masterAgentId.set(m.agent_id);
        void this.router.navigate(['/agents', m.agent_id, 'chat'], {
          queryParams: q ? { q } : {},
        });
      },
      error: () => this.sendingMaster.set(false),
    });
  }

  private loadAgents(): void {
    this.agentsApi.list().subscribe({
      next: (list) => {
        // ARIES Master đã có ô chat riêng ở trên — không lặp lại trong danh sách.
        this.agents.set(list.filter((a) => !this.isMasterAgent(a)));
      },
      complete: () => this.loadingAgents.set(false),
      error: () => this.loadingAgents.set(false),
    });
  }
}

import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Router, RouterLink } from '@angular/router';
import { AgentsApi, AgentSummary, SessionService } from '@aiagent/core';
import { Tag } from '@aiagent/ui';

type FilterMode = 'all' | 'published' | 'draft';

const IDEA_PRESETS = [
  'Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị',
  'Trợ lý CSKH giải đáp chính sách bảo hành & đổi trả, lịch sự, không tự ý hứa hoàn tiền',
  'Trợ lý IT Helpdesk hướng dẫn xử lý sự cố mạng và tài khoản nội bộ theo từng bước',
];

@Component({
  selector: 'app-agents-list',
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
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="robot_2" />
        </span>
        <div class="hero-copy">
          <h1>Agent</h1>
          <p class="subtitle">
            Khởi tạo AI Agent mới bằng mô tả tự nhiên hoặc biểu mẫu, quản lý và
            xuất bản cho tổ chức.
          </p>
        </div>
      </div>
      @if (canCreate()) {
        <div class="hero-actions">
          <a mat-stroked-button class="pill-btn" routerLink="/agents/new">
            <mat-icon svgIcon="add" /> Tạo bằng form
          </a>
          <a mat-flat-button class="btn-aries" routerLink="/builder">
            <mat-icon svgIcon="bolt" /> Mở Agent Architect
          </a>
        </div>
      }
    </div>

    <!-- Khu vực Tạo Agent đặt tại Tab Agent -->
    @if (canCreate()) {
      <section class="ask-card">
        <div class="ask-title">
          <span class="icon-badge accent">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
          </span>
          <div>
            <label for="ask">Bạn muốn tạo AI Agent gì hôm nay?</label>
            <p class="muted small">
              Mô tả agent bằng lời — ARIES Architect sẽ dựng cấu hình, tự chạy
              thử (Eval) và vận hành cho bạn. Mẹo: Ctrl+K để mở command bar.
            </p>
          </div>
        </div>

        <div class="composer-box">
          <textarea
            id="ask"
            rows="2"
            [(ngModel)]="idea"
            (keydown.enter)="$event.preventDefault(); createByIdea()"
            placeholder="Ví dụ: Trợ lý nội bộ trả lời giờ làm việc ở các văn phòng, ngắn gọn, không bàn chuyện chính trị…"
          ></textarea>
          <div class="composer-bar">
            <div class="composer-hints">
              <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
              <span
                >Nhấn <kbd>Enter</kbd> để dựng Agent tự động với Architect</span
              >
            </div>
            <button
              mat-flat-button
              class="btn-aries"
              type="button"
              [disabled]="!idea.trim()"
              (click)="createByIdea()"
            >
              <mat-icon svgIcon="bolt" /> Tạo Agent
            </button>
          </div>
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

    @if (!loading() && agents().length > 0) {
      <div class="filter-bar">
        <div class="sub-tabs" role="tablist" aria-label="Lọc theo trạng thái">
          <button
            type="button"
            [class.active]="filter() === 'all'"
            (click)="filter.set('all')"
          >
            Tất cả · {{ agents().length }}
          </button>
          <button
            type="button"
            [class.active]="filter() === 'published'"
            (click)="filter.set('published')"
          >
            Đã publish · {{ publishedCount() }}
          </button>
          <button
            type="button"
            [class.active]="filter() === 'draft'"
            (click)="filter.set('draft')"
          >
            Nháp · {{ draftCount() }}
          </button>
        </div>

        <div class="search-box">
          <mat-icon class="sm" svgIcon="search" aria-hidden="true" />
          <input
            type="search"
            [ngModel]="query()"
            (ngModelChange)="query.set($event)"
            placeholder="Tìm theo tên hoặc mô tả…"
            aria-label="Tìm kiếm agent"
          />
        </div>
      </div>

      <div class="flow-info-strip">
        <mat-icon class="sm" svgIcon="verified" aria-hidden="true" />
        <span>
          Mỗi Agent được kiểm soát phiên bản độc lập (AgentSpec v2). Bản nháp có
          thể thử nghiệm trực tiếp trước khi Publish.
        </span>
      </div>
    }

    @if (loading()) {
      <p class="muted">Đang tải…</p>
    } @else if (agents().length === 0) {
      <div class="empty-state-card">
        <div class="empty-avatar">
          <img
            src="assets/brand/aries-mark-v3.png?v=aries4"
            alt="ARIES"
            width="64"
            height="64"
          />
        </div>
        <h2>
          Chưa có agent nào{{ canCreate() ? '' : ' được chia sẻ với bạn' }}
        </h2>
        @if (canCreate()) {
          <p>
            Nhập mô tả vào khung bên trên hoặc tạo bằng biểu mẫu để bắt đầu.
          </p>
          <div class="empty-actions">
            <a mat-flat-button class="btn-aries" routerLink="/builder">
              <mat-icon svgIcon="bolt" /> Tạo bằng mô tả
            </a>
            <a mat-stroked-button class="pill-btn" routerLink="/agents/new">
              <mat-icon svgIcon="add" /> Tạo bằng form
            </a>
          </div>
        }
      </div>
    } @else if (filteredAgents().length === 0) {
      <div class="empty-state-card">
        <div class="empty-icon-ring" aria-hidden="true">
          <mat-icon svgIcon="search" />
        </div>
        <h2>Không tìm thấy agent phù hợp</h2>
        <p>
          Không có agent nào khớp với từ khóa hoặc bộ lọc hiện tại. Hãy thử thay
          đổi điều kiện lọc.
        </p>
        <div class="empty-actions">
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            (click)="query.set(''); filter.set('all')"
          >
            <mat-icon svgIcon="refresh" /> Xóa bộ lọc
          </button>
        </div>
      </div>
    } @else {
      <div class="grid">
        @for (a of filteredAgents(); track a.id) {
          <article class="flow-agent-card">
            <div class="card-top">
              <span class="agent-avatar" aria-hidden="true">
                <mat-icon class="sm" svgIcon="robot_2" />
              </span>
              <div class="card-body">
                <div class="title-line">
                  <strong>{{ a.name }}</strong>
                  <ui-tag value="On-prem" severity="warn" />
                  <ui-tag
                    [value]="statusLabel(a)"
                    [severity]="a.published_version ? 'success' : 'secondary'"
                  />
                </div>
                <p class="desc">
                  {{ a.description || 'Không có mô tả chi tiết.' }}
                </p>
              </div>
            </div>

            <div class="chips-row">
              <span class="flow-chip chip-read">
                <mat-icon class="sm" svgIcon="code" aria-hidden="true" />
                latest: v{{ a.latest_version }}
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
                    <mat-icon svgIcon="edit" /> Sửa
                  </a>
                  <button
                    mat-icon-button
                    type="button"
                    class="del-btn"
                    [disabled]="deleting() === a.id"
                    [attr.aria-label]="'Xóa agent ' + a.name"
                    title="Xóa agent"
                    (click)="remove(a)"
                  >
                    <mat-icon svgIcon="delete" />
                  </button>
                }
                <a
                  mat-flat-button
                  class="btn-aries"
                  [routerLink]="['/agents', a.id, 'chat']"
                  [queryParams]="a.published_version ? {} : { draft: 1 }"
                >
                  <mat-icon svgIcon="forum" /> Chat
                </a>
              </div>
            </div>
          </article>
        }
      </div>
    }
  `,
  styles: `
    .page-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1rem;
      h1 {
        font-size: 1.35rem;
        margin: 0 0 0.15rem;
      }
      .subtitle {
        margin: 0;
        font-size: 0.88rem;
      }
    }
    .actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .pill-btn {
      border-radius: 999px !important;
    }
    .ask-card {
      margin-bottom: 1.25rem;
      padding: 1.15rem 1.25rem;
      border-radius: var(--app-radius-xl);
      background: var(--app-surface);
      border: 1px solid var(--app-border-strong);
      box-shadow: var(--app-shadow-md);
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
    .filter-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      margin-bottom: 0.85rem;
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
      min-width: 240px;
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
    .grid {
      display: grid;
      gap: 1.1rem;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    }
    .flow-agent-card {
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
    .chips-row {
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
        font-size: 0.8rem;
        color: var(--app-muted);
      }
    }
    .foot-actions {
      display: flex;
      gap: 0.45rem;
      align-items: center;
    }
    .del-btn {
      color: var(--app-muted);
      &:hover {
        color: var(--app-danger, #dc2626);
      }
    }
    .empty-card {
      text-align: center;
      padding: 2.25rem 1.5rem;
      .empty-actions {
        display: flex;
        justify-content: center;
        gap: 0.6rem;
        margin-top: 1rem;
      }
    }
  `,
})
export class AgentsList implements OnInit {
  private readonly api = inject(AgentsApi);
  private readonly session = inject(SessionService);
  private readonly router = inject(Router);

  protected readonly presets = IDEA_PRESETS;
  protected idea = '';

  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly loading = signal(true);
  protected readonly query = signal('');
  protected readonly filter = signal<FilterMode>('all');

  protected readonly publishedCount = computed(
    () => this.agents().filter((a) => !!a.published_version).length,
  );
  protected readonly draftCount = computed(
    () => this.agents().filter((a) => !a.published_version).length,
  );

  protected readonly filteredAgents = computed(() => {
    const q = this.query().trim().toLowerCase();
    const mode = this.filter();
    return this.agents().filter((a) => {
      if (mode === 'published' && !a.published_version) return false;
      if (mode === 'draft' && !!a.published_version) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description ?? '').toLowerCase().includes(q)
      );
    });
  });

  ngOnInit(): void {
    this.session.load().catch(() => undefined);
    this.api.list().subscribe({
      next: (a) => this.agents.set(a),
      complete: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  protected readonly deleting = signal<string | null>(null);

  protected remove(a: AgentSummary): void {
    const live = a.published_version
      ? ' Agent đang publish: link chia sẻ, API key, Telegram và lịch chạy của nó sẽ ngừng hoạt động.'
      : '';
    if (
      !confirm(
        `Xóa agent “${a.name}”?${live} Lịch sử hội thoại vẫn được giữ cho mục đích audit.`,
      )
    )
      return;
    this.deleting.set(a.id);
    this.api.archive(a.id).subscribe({
      next: () => {
        this.deleting.set(null);
        this.agents.update((list) => list.filter((x) => x.id !== a.id));
      },
      error: (e: { error?: { error?: { message?: string } } }) => {
        this.deleting.set(null);
        alert(e.error?.error?.message ?? 'Không xóa được agent.');
      },
    });
  }

  protected usePreset(preset: string): void {
    this.idea = preset;
  }

  protected createByIdea(): void {
    const q = this.idea.trim();
    if (q) void this.router.navigate(['/builder'], { queryParams: { q } });
  }

  protected canCreate(): boolean {
    return this.session.hasAnyRole('builder', 'admin');
  }

  protected statusLabel(a: AgentSummary): string {
    if (a.status === 'archived') return 'Lưu trữ';
    return a.published_version ? 'Đã publish' : 'Nháp';
  }
}

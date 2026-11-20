import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import { AgentsApi, AgentSummary, SessionService } from '@aiagent/core';
import { Tag } from '@aiagent/ui';

type FilterMode = 'all' | 'published' | 'draft';

@Component({
  selector: 'app-agents-list',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatIcon,
    Tag,
  ],
  template: `
    <div class="page-head">
      <div>
        <h1>Agent</h1>
        <p class="muted subtitle">
          Quản lý các AI Agent đã tạo, trò chuyện thử nghiệm hoặc xuất bản cho
          tổ chức.
        </p>
      </div>
      @if (canCreate()) {
        <div class="actions">
          <a mat-stroked-button class="pill-btn" routerLink="/agents/new">
            <mat-icon svgIcon="add" /> Tạo bằng form
          </a>
          <a mat-flat-button class="btn-aries" routerLink="/builder">
            <mat-icon svgIcon="bolt" /> Tạo bằng mô tả
          </a>
        </div>
      }
    </div>

    @if (!loading() && agents().length > 0) {
      <div class="filter-bar">
        <div
          class="sub-tabs"
          role="tablist"
          aria-label="Lọc theo trạng thái"
        >
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
        Mỗi Agent được kiểm soát phiên bản độc lập (AgentSpec v2). Bản nháp có
        thể thử nghiệm trực tiếp trước khi Publish.
      </div>
    }

    @if (loading()) {
      <p class="muted">Đang tải…</p>
    } @else if (agents().length === 0) {
      <mat-card appearance="outlined" class="empty-card">
        <mat-card-content>
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
            <p class="muted">
              Tạo agent đầu tiên bằng mô tả (Agent Architect) hoặc bằng form.
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
        </mat-card-content>
      </mat-card>
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
      margin-bottom: 0.9rem;
      h1 {
        font-size: 1.25rem;
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

  protected canCreate(): boolean {
    return this.session.hasAnyRole('builder', 'admin');
  }

  protected statusLabel(a: AgentSummary): string {
    if (a.status === 'archived') return 'Lưu trữ';
    return a.published_version ? 'Đã publish' : 'Nháp';
  }
}

import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import { ApiService, PluginInfo } from '@aiagent/core';
import { AppIcon, Severity, Tag } from '@aiagent/ui';
import { catchError, of } from 'rxjs';

type PluginFilter = 'all' | 'skill' | 'connector' | 'active';

const PLUGIN_DESC: Record<string, string> = {
  clock:
    'Cung cấp thời gian hệ thống và múi giờ chuẩn cho AI Agent trong các tác vụ tra cứu lịch và báo cáo.',
  echo: 'Skill kiểm thử phản hồi nhanh (echo) phục vụ chẩn đoán luồng gọi tool và kiểm thử hội thoại.',
  web: 'Tìm kiếm thông tin qua SearXNG nội bộ và trích xuất nội dung trang web có bộ lọc chống SSRF.',
  postgres:
    'Kết nối cơ sở dữ liệu PostgreSQL với bộ bảo vệ SQL Guard, phân tách truy vấn đọc và thực thi.',
};

@Component({
  selector: 'app-plugins',
  imports: [RouterLink, MatButtonModule, MatIcon, Tag],
  template: `
    <div class="flow-status-card">
      <div class="status-left">
        <div class="status-logo">
          <img
            src="assets/brand/aries-mark-v3.png?v=aries4"
            alt="ARIES"
            width="42"
            height="42"
          />
        </div>
        <div>
          <div class="status-title-row">
            <strong>ARIES Plugin Engine</strong>
            <span class="run-pill">● Đang chạy</span>
          </div>
          <div class="muted small">
            {{ (plugins() ?? []).length }} plugin đã nạp · cô lập lỗi entry-point ·
            on-premise
          </div>
        </div>
      </div>
      <div class="status-actions">
        <a mat-stroked-button class="pill-btn" routerLink="/agents">
          <mat-icon svgIcon="robot_2" /> Danh sách Agent
        </a>
        <a mat-stroked-button class="pill-btn" routerLink="/builder">
          <mat-icon svgIcon="bolt" /> Agent Architect
        </a>
      </div>
    </div>

    <div class="section-head">
      <div>
        <h1>Plugin</h1>
        <p class="muted subtitle">
          Quản lý các Skill và Connector đã cài đặt, xem danh sách tool và mức
          độ rủi ro.
        </p>
      </div>
    </div>

    <div class="sub-tabs" role="tablist" aria-label="Lọc loại plugin">
      <button
        type="button"
        [class.active]="filter() === 'all'"
        (click)="filter.set('all')"
      >
        Tất cả · {{ (plugins() ?? []).length }}
      </button>
      <button
        type="button"
        [class.active]="filter() === 'skill'"
        (click)="filter.set('skill')"
      >
        Skill · {{ skillCount() }}
      </button>
      <button
        type="button"
        [class.active]="filter() === 'connector'"
        (click)="filter.set('connector')"
      >
        Connector · {{ connectorCount() }}
      </button>
      <button
        type="button"
        [class.active]="filter() === 'active'"
        (click)="filter.set('active')"
      >
        Đang bật · {{ activeCount() }}
      </button>
    </div>

    <div class="flow-info-strip">
      Plugin mở rộng ARIES với các tool, skill và connector tùy chỉnh. Các
      plugin lỗi tự động bị cô lập khi khởi động để không làm gián đoạn hệ
      thống.
    </div>

    @if (filteredPlugins().length === 0) {
      <div class="empty-box muted">Không có plugin nào.</div>
    } @else {
      <div class="plugin-grid">
        @for (p of filteredPlugins(); track p.id) {
          <article class="plugin-card">
            <div class="card-top">
              <span class="plugin-icon" aria-hidden="true">
                <mat-icon class="sm" [svgIcon]="pluginIcon(p)" />
              </span>
              <div class="card-body">
                <div class="title-line">
                  <strong>{{ p.name ?? p.entry_point }}</strong>
                  <ui-tag
                    [value]="p.kind ?? 'plugin'"
                    [severity]="p.kind === 'connector' ? 'warn' : 'primary'"
                  />
                  <ui-tag
                    [value]="p.status"
                    [severity]="statusSeverity(p)"
                  />
                </div>
                <p class="desc">
                  {{ descriptionFor(p) }}
                </p>
              </div>
            </div>

            <div class="tools-area">
              @for (t of p.tools; track t.name) {
                <span class="flow-chip" [class]="chipClass(t.risk)">
                  <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
                  <span>{{ t.name }}</span>
                  <small class="risk-tag">{{ t.risk }}</small>
                </span>
              }
              <span class="flow-chip chip-warn">
                <mat-icon class="sm" svgIcon="code" aria-hidden="true" />
                <span>/{{ p.id ?? p.entry_point }}</span>
              </span>
            </div>

            <div class="card-foot">
              <span class="ver">v{{ p.version ?? '1.0.0' }}</span>
              <span
                class="state-pill"
                [class.is-active]="p.status === 'active'"
              >
                <mat-icon
                  class="sm"
                  [svgIcon]="p.status === 'active' ? 'check_circle' : 'cancel'"
                  aria-hidden="true"
                />
                {{ p.status === 'active' ? 'Enabled' : p.status }}
              </span>
            </div>
          </article>
        }
      </div>
    }
  `,
  styles: `
    .status-left {
      display: flex;
      align-items: center;
      gap: 0.9rem;
    }
    .status-logo {
      width: 46px;
      height: 46px;
      border-radius: 14px;
      background: var(--app-highlight);
      border: 1px solid rgba(255, 61, 139, 0.28);
      display: flex;
      align-items: center;
      justify-content: center;
      img {
        border-radius: 50%;
      }
    }
    .status-title-row {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      font-size: 1.02rem;
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
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 0.85rem;
      h1 {
        font-size: 1.25rem;
        margin: 0 0 0.15rem;
      }
      .subtitle {
        margin: 0;
        font-size: 0.88rem;
      }
    }
    .sub-tabs {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-wrap: wrap;
      margin-bottom: 0.85rem;
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
        &:hover {
          color: var(--app-text);
          background: var(--app-surface-2);
        }
        &.active {
          background: var(--app-surface);
          color: var(--app-text);
          font-weight: 600;
          border-color: var(--app-border);
          box-shadow: var(--app-shadow-sm);
        }
      }
    }
    .plugin-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 1.1rem;
    }
    .plugin-card {
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
    .plugin-icon {
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      color: var(--app-primary);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
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
      strong {
        font-size: 1rem;
      }
    }
    .desc {
      margin: 0;
      font-size: 0.85rem;
      color: var(--app-muted);
      line-height: 1.48;
    }
    .tools-area {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }
    .risk-tag {
      opacity: 0.75;
      font-size: 0.7rem;
      margin-left: 0.15rem;
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
    .state-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      padding: 0.28rem 0.75rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      background: var(--app-surface-2);
      color: var(--app-muted);
      &.is-active {
        background: var(--app-highlight);
        color: var(--app-primary);
        border: 1px solid rgba(255, 61, 139, 0.28);
      }
    }
    .empty-box {
      padding: 2.5rem;
      text-align: center;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-lg);
    }
  `,
})
export class Plugins {
  protected readonly filter = signal<PluginFilter>('all');
  protected readonly plugins = toSignal(
    inject(ApiService)
      .plugins()
      .pipe(catchError(() => of<PluginInfo[]>([]))),
  );

  protected readonly skillCount = computed(
    () => (this.plugins() ?? []).filter((p) => p.kind === 'skill').length,
  );
  protected readonly connectorCount = computed(
    () => (this.plugins() ?? []).filter((p) => p.kind === 'connector').length,
  );
  protected readonly activeCount = computed(
    () => (this.plugins() ?? []).filter((p) => p.status === 'active').length,
  );

  protected readonly filteredPlugins = computed(() => {
    const list = this.plugins() ?? [];
    const f = this.filter();
    if (f === 'skill') return list.filter((p) => p.kind === 'skill');
    if (f === 'connector') return list.filter((p) => p.kind === 'connector');
    if (f === 'active') return list.filter((p) => p.status === 'active');
    return list;
  });

  protected pluginIcon(p: PluginInfo): AppIcon {
    const id = (p.id ?? p.entry_point ?? '').toLowerCase();
    if (id.includes('postgres') || p.kind === 'connector') return 'database';
    if (id.includes('web')) return 'travel_explore';
    if (id.includes('clock')) return 'bolt';
    return 'deployed_code';
  }

  protected descriptionFor(p: PluginInfo): string {
    if (p.reason) return p.reason;
    const key = p.id ?? p.entry_point ?? '';
    return (
      PLUGIN_DESC[key] ??
      `Plugin ${p.name ?? key} được nạp qua entry point (${p.entry_point}).`
    );
  }

  protected chipClass(risk: string): string {
    switch (risk) {
      case 'read':
        return 'chip-read';
      case 'write':
        return 'chip-write';
      case 'external_send':
        return 'chip-warn';
      default:
        return 'chip-danger';
    }
  }

  protected statusSeverity(p: PluginInfo): Severity {
    return p.status === 'active'
      ? 'success'
      : p.status === 'disabled'
        ? 'warn'
        : 'danger';
  }
}

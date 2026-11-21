import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import {
  ModelProviderInfo,
  ModelCheckResult,
  ModelProviderProbeResult,
  ModelProvidersApi,
  SessionService,
} from '@aiagent/core';
import { Notice, Tag } from '@aiagent/ui';

type FilterMode = 'all' | 'active' | 'available' | 'local';

const BADGE_LABEL: Record<string, string> = {
  gemini: 'G',
  ollama: 'OL',
  openai: 'OA',
  anthropic: 'CL',
  groq: 'GQ',
  openrouter: 'OR',
  deepseek: 'DS',
};

/** Trang quản lý nhà cung cấp LLM (lấy cảm hứng từ Dify Model Providers). */
@Component({
  selector: 'app-model-providers',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatProgressBarModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="dns" />
        </span>
        <div class="hero-copy">
          <h1>Nhà cung cấp LLM</h1>
          <p class="subtitle">
            Kết nối trực tiếp các mô hình ngôn ngữ lớn (Google Gemini, Ollama
            nội bộ, OpenAI, Claude, Groq, OpenRouter, DeepSeek) để sử dụng ngay
            cho AI Agent mà không cần khởi động lại hệ thống.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <button
          mat-stroked-button
          class="pill-btn"
          type="button"
          [disabled]="loading()"
          (click)="reload()"
        >
          <mat-icon svgIcon="refresh" />
          {{ loading() ? 'Đang tải…' : 'Tải lại' }}
        </button>
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="check_circle" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val"
            >{{ activeProviders().length }}/{{ providers().length }}</span
          >
          <span class="kpi-label">Nhà cung cấp đã kết nối</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="dns" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ totalModels() }}</span>
          <span class="kpi-label">Model sẵn sàng sử dụng</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon warn" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ localActiveCount() }}</span>
          <span class="kpi-label">Provider tự host (restricted)</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon" aria-hidden="true">
          <mat-icon class="sm" svgIcon="lock" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">OpenBao</span>
          <span class="kpi-label">Mã hóa API Key</span>
        </div>
      </div>
    </div>

    @if (!isAdmin()) {
      <div class="flow-info-strip">
        <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
        <span>
          Bạn đang xem ở chế độ chỉ đọc. Chỉ <strong>Admin</strong> mới có quyền
          thêm, sửa hoặc ngắt kết nối nhà cung cấp LLM.
        </span>
      </div>
    }

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }
    @if (info()) {
      <ui-notice severity="success">{{ info() }}</ui-notice>
    }
    @if (loading()) {
      <mat-progress-bar mode="indeterminate" class="mp-progress" />
    }

    <!-- Bảng cấu hình kết nối (kiểu Dify Setup Modal) -->
    @if (editing(); as p) {
      <form class="panel-card setup-panel" (ngSubmit)="save()">
        <div class="setup-head">
          <div class="setup-head-left">
            <span class="brand-badge" [attr.data-p]="p.id">{{
              badge(p.id)
            }}</span>
            <div>
              <div class="title-row">
                <h2>Cấu hình {{ p.name }}</h2>
                <ui-tag
                  [value]="p.kind === 'local' ? 'Tự host (Local)' : 'Cloud API'"
                  [severity]="p.kind === 'local' ? 'success' : 'info'"
                />
              </div>
              <p class="muted small">
                {{ p.description }}
                <a
                  class="docs-link"
                  [href]="p.docs_url"
                  target="_blank"
                  rel="noopener"
                >
                  Lấy API Key / Tài liệu ↗
                </a>
              </p>
            </div>
          </div>
          <button
            mat-icon-button
            type="button"
            aria-label="Đóng"
            (click)="cancelEdit()"
          >
            <mat-icon svgIcon="close" />
          </button>
        </div>

        <div class="setup-grid">
          @if (p.requires_api_key) {
            <mat-form-field appearance="outline">
              <mat-icon matPrefix class="sm" svgIcon="key" />
              <mat-label>API Key</mat-label>
              <input
                matInput
                name="apiKey"
                [type]="showKey() ? 'text' : 'password'"
                autocomplete="off"
                [(ngModel)]="form.api_key"
                [placeholder]="
                  p.has_api_key
                    ? 'Đã lưu trong OpenBao — để trống để giữ nguyên'
                    : 'Dán API key của ' + p.name
                "
              />
              <button
                mat-icon-button
                matSuffix
                type="button"
                (click)="showKey.set(!showKey())"
                [attr.aria-label]="showKey() ? 'Ẩn khóa' : 'Hiện khóa'"
              >
                <mat-icon class="sm" [svgIcon]="showKey() ? 'lock' : 'key'" />
              </button>
            </mat-form-field>
          }

          <mat-form-field appearance="outline">
            <mat-icon matPrefix class="sm" svgIcon="link" />
            <mat-label>
              Base URL{{ p.requires_base_url ? ' (bắt buộc)' : ' (tùy chọn)' }}
            </mat-label>
            <input
              matInput
              name="baseUrl"
              [(ngModel)]="form.base_url"
              [required]="p.requires_base_url"
              [placeholder]="p.default_base_url"
            />
          </mat-form-field>
        </div>

        <mat-form-field appearance="outline" class="full">
          <mat-icon matPrefix class="sm" svgIcon="dns" />
          <mat-label
            >Danh sách Model tùy chỉnh (cách nhau bằng dấu phẩy — để trống để tự
            động khám phá)</mat-label
          >
          <input
            matInput
            name="models"
            [(ngModel)]="form.modelsText"
            [placeholder]="'Ví dụ: ' + p.sample_models.join(', ')"
          />
        </mat-form-field>

        <div class="presets sample-row">
          <span class="presets-label">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
            Gợi ý nhanh:
          </span>
          @for (m of p.sample_models; track m) {
            <button type="button" class="preset-chip" (click)="addSample(m)">
              <mat-icon class="sm" svgIcon="add" aria-hidden="true" />
              <span>{{ m }}</span>
            </button>
          }
        </div>

        <div class="setup-toggles">
          <mat-checkbox name="enabled" [(ngModel)]="form.enabled">
            Kích hoạt nhà cung cấp này ngay sau khi lưu
          </mat-checkbox>
          @if (p.supports_free_only) {
            <mat-checkbox name="freeOnly" [(ngModel)]="form.free_only">
              Chỉ tự động lấy các model miễn phí (<code>:free</code>)
            </mat-checkbox>
          }
        </div>

        @if (probe(); as pr) {
          <div class="probe-box" [class.ok]="pr.ok" [class.err]="!pr.ok">
            <div class="probe-head">
              <mat-icon
                class="sm"
                [svgIcon]="pr.ok ? 'check_circle' : 'warning'"
              />
              @if (pr.ok) {
                <strong>
                  Kết nối thành công · {{ pr.latency_ms }} ms · phát hiện
                  {{ pr.models.length }} model
                </strong>
              } @else {
                <strong>{{ pr.error || 'Kết nối thất bại.' }}</strong>
              }
            </div>
            @if (pr.ok && pr.models.length > 0) {
              <div class="probe-tools">
                <span class="muted small">
                  Đã chọn {{ selectedCount(pr.models) }}/{{ pr.models.length }}
                  model — nhấn vào model để bỏ/chọn.
                </span>
                <button
                  type="button"
                  class="link-btn"
                  (click)="selectAll(pr.models)"
                >
                  Chọn tất cả
                </button>
                <button type="button" class="link-btn" (click)="clearModels()">
                  Bỏ chọn tất cả
                </button>
                <button
                  type="button"
                  class="link-btn"
                  [disabled]="checking()"
                  (click)="checkSelected()"
                  title="Gọi thử từng model đang chọn; model lỗi sẽ tự bỏ chọn"
                >
                  {{ checking() ? 'Đang kiểm tra…' : 'Kiểm tra từng model' }}
                </button>
              </div>
              @if (checkSummary(); as s) {
                <p class="muted small check-summary">{{ s }}</p>
              }
              <div class="chips-wrap probe-chips">
                @for (m of pr.models; track m) {
                  <button
                    type="button"
                    class="preset-chip"
                    [class.selected]="isSelected(m)"
                    [class.bad]="checks()[m]?.ok === false"
                    [class.good]="checks()[m]?.ok === true"
                    [attr.aria-pressed]="isSelected(m)"
                    [title]="checkTitle(m)"
                    (click)="toggleModel(m)"
                  >
                    @if (checks()[m]; as c) {
                      <span aria-hidden="true">{{ c.ok ? '✓' : '✗' }}</span>
                    }
                    <span>{{ m }}</span>
                  </button>
                }
              </div>
            }
          </div>
        }

        <div class="setup-actions">
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            [disabled]="testing() || saving()"
            (click)="testForm()"
          >
            <mat-icon svgIcon="play_arrow" />
            {{ testing() ? 'Đang kiểm tra…' : 'Kiểm tra kết nối' }}
          </button>
          <span class="spacer"></span>
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            (click)="cancelEdit()"
          >
            Hủy
          </button>
          <button
            mat-flat-button
            class="btn-aries"
            type="submit"
            [disabled]="saving()"
          >
            <mat-icon svgIcon="save" />
            {{ saving() ? 'Đang lưu…' : 'Lưu & Kích hoạt' }}
          </button>
        </div>
      </form>
    }

    <!-- Bộ lọc -->
    <div class="filter-row">
      <div class="aries-sub-tabs" role="tablist" aria-label="Lọc nhà cung cấp">
        <button
          type="button"
          [class.active]="filter() === 'all'"
          (click)="filter.set('all')"
        >
          Tất cả · {{ providers().length }}
        </button>
        <button
          type="button"
          [class.active]="filter() === 'active'"
          (click)="filter.set('active')"
        >
          Đã kết nối · {{ activeProviders().length }}
        </button>
        <button
          type="button"
          [class.active]="filter() === 'available'"
          (click)="filter.set('available')"
        >
          Chưa kết nối · {{ inactiveProviders().length }}
        </button>
        <button
          type="button"
          [class.active]="filter() === 'local'"
          (click)="filter.set('local')"
        >
          Tự host (Local)
        </button>
      </div>
    </div>

    <!-- Nhóm 1: Đã kết nối -->
    @if (visibleActive().length > 0) {
      <h2 class="group-title">
        <mat-icon class="sm" svgIcon="check_circle" aria-hidden="true" />
        <span>Đã kết nối</span>
      </h2>
      <div class="providers-grid">
        @for (p of visibleActive(); track p.id) {
          <article class="panel-card provider-card is-active">
            <div class="card-top">
              <span class="brand-badge" [attr.data-p]="p.id">{{
                badge(p.id)
              }}</span>
              <div class="card-info">
                <div class="title-row">
                  <strong>{{ p.name }}</strong>
                  <span class="run-pill">● Đang hoạt động</span>
                </div>
                <div class="tag-row">
                  <ui-tag
                    [value]="p.kind === 'local' ? 'Local / On-prem' : 'Cloud'"
                    [severity]="p.kind === 'local' ? 'success' : 'info'"
                  />
                  <ui-tag
                    [value]="
                      p.source === 'db' ? 'Cấu hình UI' : 'Biến môi trường'
                    "
                    severity="secondary"
                  />
                </div>
              </div>
            </div>

            <p class="desc">{{ p.description }}</p>

            <div class="url-line">
              <mat-icon class="xs" svgIcon="link" aria-hidden="true" />
              <code>{{ p.base_url }}</code>
            </div>

            <div class="models-block">
              <span class="models-label"
                >Model sẵn sàng ({{ p.available_models.length }})</span
              >
              <div class="chips-wrap">
                @for (
                  m of isExpanded(p.id)
                    ? p.available_models
                    : p.available_models.slice(0, 5);
                  track m
                ) {
                  <span class="flow-chip chip-read">{{ m }}</span>
                } @empty {
                  <span class="muted small"
                    >Chưa phát hiện model — bấm Kiểm tra hoặc thêm thủ
                    công.</span
                  >
                }
                @if (p.available_models.length > 5) {
                  <button
                    type="button"
                    class="more-link"
                    (click)="toggleExpand(p.id)"
                  >
                    {{
                      isExpanded(p.id)
                        ? 'Thu gọn'
                        : '+' + (p.available_models.length - 5) + ' model khác'
                    }}
                  </button>
                }
              </div>
            </div>

            @if (isAdmin()) {
              <div class="card-foot">
                <button
                  mat-stroked-button
                  class="pill-btn"
                  type="button"
                  [disabled]="testingId() === p.id"
                  (click)="quickTest(p)"
                >
                  <mat-icon svgIcon="play_arrow" />
                  {{ testingId() === p.id ? 'Đang thử…' : 'Kiểm tra' }}
                </button>
                <div class="foot-right">
                  @if (p.source === 'db') {
                    <button
                      mat-stroked-button
                      class="pill-btn danger-btn"
                      type="button"
                      (click)="disconnect(p)"
                    >
                      Ngắt
                    </button>
                  }
                  <button
                    mat-flat-button
                    class="btn-aries"
                    type="button"
                    (click)="startEdit(p)"
                  >
                    <mat-icon svgIcon="tune" /> Cấu hình
                  </button>
                </div>
              </div>
            }
          </article>
        }
      </div>
    }

    <!-- Nhóm 2: Kho nhà cung cấp chưa kết nối -->
    @if (visibleInactive().length > 0) {
      <h2 class="group-title">
        <mat-icon class="sm" svgIcon="add" aria-hidden="true" />
        <span>Thêm nhà cung cấp LLM</span>
      </h2>
      <div class="providers-grid">
        @for (p of visibleInactive(); track p.id) {
          <article class="panel-card provider-card">
            <div class="card-top">
              <span class="brand-badge" [attr.data-p]="p.id">{{
                badge(p.id)
              }}</span>
              <div class="card-info">
                <div class="title-row">
                  <strong>{{ p.name }}</strong>
                  <ui-tag
                    [value]="p.kind === 'local' ? 'Local / On-prem' : 'Cloud'"
                    [severity]="p.kind === 'local' ? 'success' : 'secondary'"
                  />
                </div>
                <span class="muted small">{{ p.vendor }}</span>
              </div>
            </div>

            <p class="desc">{{ p.description }}</p>

            <div class="chips-wrap">
              @for (m of p.sample_models; track m) {
                <span class="flow-chip chip-write">{{ m }}</span>
              }
            </div>

            <div class="card-foot">
              <a
                class="docs-link small"
                [href]="p.docs_url"
                target="_blank"
                rel="noopener"
              >
                Tài liệu ↗
              </a>
              @if (isAdmin()) {
                <button
                  mat-flat-button
                  class="btn-aries"
                  type="button"
                  (click)="startEdit(p)"
                >
                  <mat-icon svgIcon="add" /> Kết nối
                </button>
              }
            </div>
          </article>
        }
      </div>
    }
  `,
  styles: `
    .mp-progress {
      border-radius: 999px;
      margin-bottom: 1rem;
    }
    .filter-row {
      margin-bottom: 1.1rem;
    }
    .group-title {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      margin: 0.25rem 0 0.8rem;
      font-size: 1rem;
      mat-icon {
        color: var(--app-primary);
      }
    }
    .providers-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .provider-card {
      display: flex;
      flex-direction: column;
      gap: 0.8rem;
      transition: all 0.18s ease;
      &:hover {
        border-color: var(--app-border-strong);
        box-shadow: var(--app-shadow-md);
        transform: translateY(-2px);
      }
      &.is-active {
        border-color: rgba(255, 61, 139, 0.35);
      }
    }
    .card-top,
    .setup-head-left {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
    }
    .card-info {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      min-width: 0;
      flex: 1;
    }
    .title-row,
    .tag-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .brand-badge {
      width: 42px;
      height: 42px;
      border-radius: 12px;
      font-weight: 800;
      font-size: 0.88rem;
      letter-spacing: 0.04em;
      color: #fff;
      background: var(--aries-gradient);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      box-shadow: 0 4px 12px rgba(20, 10, 38, 0.16);
      &[data-p='gemini'] {
        background: linear-gradient(135deg, #2563eb 0%, #9333ea 100%);
      }
      &[data-p='ollama'] {
        background: linear-gradient(135deg, #059669 0%, #0d9488 100%);
      }
      &[data-p='openai'] {
        background: linear-gradient(135deg, #0f766e 0%, #334155 100%);
      }
      &[data-p='anthropic'] {
        background: linear-gradient(135deg, #d97706 0%, #e11d74 100%);
      }
      &[data-p='groq'] {
        background: linear-gradient(135deg, #ea580c 0%, #db2777 100%);
      }
      &[data-p='openrouter'] {
        background: linear-gradient(135deg, #4f46e5 0%, #c026d3 100%);
      }
      &[data-p='deepseek'] {
        background: linear-gradient(135deg, #0284c7 0%, #4338ca 100%);
      }
    }
    .desc {
      margin: 0;
      font-size: 0.85rem;
      color: var(--app-muted);
      line-height: 1.5;
      flex: 1;
    }
    .url-line {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      color: var(--app-muted);
      code {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        max-width: 100%;
      }
    }
    .models-block {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .models-label {
      font-size: 0.74rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--app-muted);
    }
    .chips-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      align-items: center;
    }
    .probe-tools {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin: 0.4rem 0;
    }
    .link-btn {
      border: none;
      background: none;
      padding: 0;
      font: inherit;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--app-accent, #e11d74);
      cursor: pointer;
    }
    .probe-chips {
      max-height: 11rem;
      overflow-y: auto;
      padding-right: 0.25rem;
    }
    .probe-chips .preset-chip {
      opacity: 0.55;
    }
    .probe-chips .preset-chip.bad {
      opacity: 0.45;
      text-decoration: line-through;
      color: var(--app-danger, #dc2626);
    }
    .probe-chips .preset-chip.good span:first-child {
      color: #16a34a;
    }
    .check-summary {
      margin: 0 0 0.4rem;
    }
    .probe-chips .preset-chip.selected {
      opacity: 1;
      border-color: var(--app-accent, #e11d74);
      box-shadow: inset 0 0 0 1px var(--app-accent, #e11d74);
    }
    .more-link {
      border: none;
      background: none;
      color: var(--app-primary);
      font: inherit;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      padding: 0.15rem 0.35rem;
      &:hover {
        text-decoration: underline;
      }
    }
    .card-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      padding-top: 0.7rem;
      border-top: 1px solid var(--app-border);
      flex-wrap: wrap;
    }
    .foot-right {
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .docs-link {
      color: var(--app-primary);
      text-decoration: none;
      font-weight: 600;
      &:hover {
        text-decoration: underline;
      }
    }

    /* Setup panel */
    .setup-panel {
      margin-bottom: 1.25rem;
      border-color: var(--app-border-strong);
      box-shadow: var(--app-shadow-md);
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .setup-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.75rem;
      h2 {
        margin: 0;
        font-size: 1.08rem;
      }
      p {
        margin: 0.2rem 0 0;
      }
    }
    .setup-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 0.25rem 0.9rem;
    }
    .sample-row {
      margin-top: -0.35rem;
    }
    .setup-toggles {
      display: flex;
      gap: 0.5rem 1.5rem;
      flex-wrap: wrap;
    }
    .probe-box {
      padding: 0.75rem 0.95rem;
      border-radius: var(--app-radius-md);
      border: 1px solid var(--app-border);
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      &.ok {
        background: var(--app-success-bg);
        border-color: var(--app-success-border);
        color: var(--app-success);
      }
      &.err {
        background: var(--app-danger-bg);
        border-color: var(--app-danger-border);
        color: var(--app-danger);
      }
    }
    .probe-head {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.86rem;
    }
    .setup-actions {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      flex-wrap: wrap;
      padding-top: 0.5rem;
      border-top: 1px solid var(--app-border);
    }
  `,
})
export class ModelProviders implements OnInit {
  private readonly api = inject(ModelProvidersApi);
  private readonly session = inject(SessionService);

  protected readonly providers = signal<ModelProviderInfo[]>([]);
  protected readonly loading = signal(false);
  protected readonly saving = signal(false);
  protected readonly testing = signal(false);
  protected readonly testingId = signal<string | null>(null);
  protected readonly filter = signal<FilterMode>('all');
  protected readonly editing = signal<ModelProviderInfo | null>(null);
  protected readonly showKey = signal(false);
  protected readonly probe = signal<ModelProviderProbeResult | null>(null);
  protected readonly checking = signal(false);
  protected readonly checks = signal<Record<string, ModelCheckResult>>({});
  protected readonly checkSummary = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly info = signal<string | null>(null);
  protected readonly expanded = signal<Set<string>>(new Set());

  protected form = {
    enabled: true,
    base_url: '',
    api_key: '',
    modelsText: '',
    free_only: true,
  };

  protected readonly isAdmin = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin');
  });
  protected readonly activeProviders = computed(() =>
    this.providers().filter((p) => p.configured),
  );
  protected readonly inactiveProviders = computed(() =>
    this.providers().filter((p) => !p.configured),
  );
  protected readonly totalModels = computed(() =>
    this.activeProviders().reduce(
      (sum, p) => sum + p.available_models.length,
      0,
    ),
  );
  protected readonly localActiveCount = computed(
    () => this.activeProviders().filter((p) => p.kind === 'local').length,
  );

  protected readonly visibleActive = computed(() => {
    const f = this.filter();
    if (f === 'available') return [];
    const list = this.activeProviders();
    return f === 'local' ? list.filter((p) => p.kind === 'local') : list;
  });

  protected readonly visibleInactive = computed(() => {
    const f = this.filter();
    if (f === 'active') return [];
    const list = this.inactiveProviders();
    return f === 'local' ? list.filter((p) => p.kind === 'local') : list;
  });

  ngOnInit(): void {
    this.session.load().catch(() => undefined);
    this.reload();
  }

  protected badge(id: string): string {
    return BADGE_LABEL[id] ?? id.slice(0, 2).toUpperCase();
  }

  protected isExpanded(id: string): boolean {
    return this.expanded().has(id);
  }

  protected toggleExpand(id: string): void {
    this.expanded.update((set) => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  protected reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.list().subscribe({
      next: (list) => this.providers.set(list),
      error: (e) => {
        this.loading.set(false);
        this.fail(e);
      },
      complete: () => this.loading.set(false),
    });
  }

  protected startEdit(p: ModelProviderInfo): void {
    this.editing.set(p);
    this.probe.set(null);
    this.checks.set({});
    this.checkSummary.set(null);
    this.showKey.set(false);
    this.error.set(null);
    this.info.set(null);
    this.form = {
      enabled: p.configured ? p.enabled : true,
      base_url: p.base_url || p.default_base_url,
      api_key: '',
      modelsText: p.custom_models.join(', '),
      free_only: Boolean(p.extra?.['free_only'] ?? true),
    };
  }

  protected cancelEdit(): void {
    this.editing.set(null);
    this.probe.set(null);
  }

  protected addSample(model: string): void {
    const cur = this.parseModels(this.form.modelsText);
    if (!cur.includes(model)) {
      this.form.modelsText = [...cur, model].join(', ');
    }
  }

  protected checkTitle(model: string): string {
    const c = this.checks()[model];
    if (!c) return 'Nhấn để bỏ/chọn';
    return c.ok
      ? `Dùng được (${c.latency_ms} ms)`
      : `Không dùng được: ${c.error ?? ''}`;
  }

  /** Gọi thử từng model đang chọn; model lỗi (ngừng cung cấp, hết quota…) tự bỏ chọn. */
  protected checkSelected(): void {
    const p = this.editing();
    const models = this.parseModels(this.form.modelsText);
    if (!p || !models.length) return;
    this.checking.set(true);
    this.checkSummary.set(null);
    this.api
      .checkModels(p.id, {
        models: models.slice(0, 100),
        base_url: this.form.base_url.trim() || null,
        api_key: this.form.api_key.trim() || null,
        extra: p.supports_free_only
          ? { free_only: this.form.free_only }
          : undefined,
      })
      .subscribe({
        next: (res) => {
          this.checking.set(false);
          const map = { ...this.checks() };
          for (const r of res) map[r.model] = r;
          this.checks.set(map);
          const bad = res.filter((r) => !r.ok).map((r) => r.model);
          this.form.modelsText = models
            .filter((m) => !bad.includes(m))
            .join(', ');
          this.checkSummary.set(
            bad.length
              ? `${res.length - bad.length}/${res.length} model dùng được — đã bỏ chọn ${bad.length} model lỗi (di chuột vào ✗ để xem lý do).`
              : `Cả ${res.length} model đều dùng được.`,
          );
        },
        error: (e) => {
          this.checking.set(false);
          this.fail(e);
        },
      });
  }

  protected isSelected(model: string): boolean {
    return this.parseModels(this.form.modelsText).includes(model);
  }

  protected selectedCount(models: string[]): number {
    const cur = this.parseModels(this.form.modelsText);
    return models.filter((m) => cur.includes(m)).length;
  }

  protected toggleModel(model: string): void {
    const cur = this.parseModels(this.form.modelsText);
    this.form.modelsText = (
      cur.includes(model) ? cur.filter((m) => m !== model) : [...cur, model]
    ).join(', ');
  }

  protected selectAll(models: string[]): void {
    const cur = this.parseModels(this.form.modelsText);
    this.form.modelsText = [
      ...cur,
      ...models.filter((m) => !cur.includes(m)),
    ].join(', ');
  }

  protected clearModels(): void {
    this.form.modelsText = '';
  }

  protected testForm(): void {
    const p = this.editing();
    if (!p) return;
    this.testing.set(true);
    this.probe.set(null);
    this.api
      .test(p.id, {
        base_url: this.form.base_url.trim() || null,
        api_key: this.form.api_key.trim() || null,
        extra: p.supports_free_only
          ? { free_only: this.form.free_only }
          : undefined,
      })
      .subscribe({
        next: (res) => {
          this.testing.set(false);
          this.probe.set(res);
          // Kết nối được → nạp sẵn toàn bộ model phát hiện được (nếu người dùng chưa tự nhập).
          if (
            res.ok &&
            res.models.length &&
            !this.parseModels(this.form.modelsText).length
          ) {
            this.selectAll(res.models);
          }
        },
        error: (e) => {
          this.testing.set(false);
          this.fail(e);
        },
      });
  }

  protected quickTest(p: ModelProviderInfo): void {
    this.testingId.set(p.id);
    this.error.set(null);
    this.info.set(null);
    this.api.test(p.id).subscribe({
      next: (res) => {
        this.testingId.set(null);
        if (res.ok) {
          this.info.set(
            `${p.name}: kết nối ổn định (${res.latency_ms} ms, phát hiện ${res.models.length} model).`,
          );
        } else {
          this.error.set(`${p.name}: ${res.error || 'Kết nối thất bại.'}`);
        }
      },
      error: (e) => {
        this.testingId.set(null);
        this.fail(e);
      },
    });
  }

  protected save(): void {
    const p = this.editing();
    if (!p) return;
    this.saving.set(true);
    this.error.set(null);
    this.info.set(null);
    this.api
      .configure(p.id, {
        enabled: this.form.enabled,
        base_url: this.form.base_url.trim() || null,
        api_key: this.form.api_key.trim() || null,
        models: this.parseModels(this.form.modelsText),
        extra: p.supports_free_only ? { free_only: this.form.free_only } : {},
      })
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.editing.set(null);
          this.probe.set(null);
          this.info.set(`Đã lưu và cập nhật kết nối ${p.name}.`);
          this.reload();
        },
        error: (e) => {
          this.saving.set(false);
          this.fail(e);
        },
      });
  }

  protected disconnect(p: ModelProviderInfo): void {
    if (
      !confirm(
        `Ngắt kết nối ${p.name}? API key lưu trong OpenBao sẽ bị xóa và các Agent dùng ${p.name} sẽ không chạy được cho tới khi kết nối lại.`,
      )
    ) {
      return;
    }
    this.api.remove(p.id).subscribe({
      next: () => {
        this.info.set(`Đã ngắt kết nối ${p.name}.`);
        this.reload();
      },
      error: (e) => this.fail(e),
    });
  }

  private parseModels(text: string): string[] {
    return text
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  private fail(err: { error?: { error?: { message?: string } } }): void {
    this.error.set(err.error?.error?.message ?? 'Thao tác thất bại.');
  }
}

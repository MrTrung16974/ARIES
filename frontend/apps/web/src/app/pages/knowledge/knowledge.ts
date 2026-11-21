import { DatePipe, DecimalPipe } from '@angular/common';
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import {
  DataClassification,
  KnowledgeApi,
  KnowledgeBaseInfo,
  KnowledgeDocument,
  KnowledgeSearchResult,
  SessionService,
} from '@aiagent/core';
import { Notice, Severity, Tag } from '@aiagent/ui';

const STATUS: Record<KnowledgeDocument['status'], [string, Severity]> = {
  pending: ['Chờ xử lý', 'secondary'],
  processing: ['Đang lập chỉ mục', 'warn'],
  ready: ['Sẵn sàng', 'success'],
  failed: ['Lỗi', 'danger'],
};

const CLS_LABEL: Record<DataClassification, [string, Severity]> = {
  public: ['Công khai', 'success'],
  internal: ['Nội bộ', 'info'],
  confidential: ['Mật', 'warn'],
  restricted: ['Hạn chế', 'danger'],
};

const ACCEPT = '.pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv,.tsv';

/** Kho tri thức (ADR-0019): tài liệu nội bộ cho agent tra cứu, có trích dẫn. */
@Component({
  selector: 'app-knowledge',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatSelectModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="menu_book" />
        </span>
        <div class="hero-copy">
          <h1>Kho tri thức</h1>
          <p class="subtitle">
            Tải tài liệu nội bộ (PDF, Word, Markdown, HTML, CSV…) để AI Agent
            tra cứu và trích dẫn nguồn chính xác. Toàn bộ dữ liệu được lập chỉ
            mục trực tiếp trên máy chủ của bạn.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <button
          mat-stroked-button
          class="pill-btn"
          type="button"
          (click)="refreshAll()"
        >
          <mat-icon svgIcon="refresh" /> Tải lại
        </button>
        @if (canCreate() && !creating()) {
          <button
            mat-flat-button
            class="btn-aries"
            type="button"
            (click)="creating.set(true)"
          >
            <mat-icon svgIcon="add" /> Tạo kho tri thức
          </button>
        }
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon" aria-hidden="true">
          <mat-icon class="sm" svgIcon="menu_book" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ kbs().length }}</span>
          <span class="kpi-label">Tổng kho tri thức</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="description" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ totalDocs() }}</span>
          <span class="kpi-label">Tổng tài liệu đã nạp</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="public" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ workspaceCount() }}</span>
          <span class="kpi-label">Kho dùng chung tổ chức</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon warn" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">On-prem</span>
          <span class="kpi-label">Lập chỉ mục cục bộ</span>
        </div>
      </div>
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }

    @if (creating()) {
      <form class="panel-card create-panel" (ngSubmit)="create()">
        <div class="panel-head">
          <div class="panel-head-left">
            <span class="icon-badge accent" aria-hidden="true">
              <mat-icon class="sm" svgIcon="add" />
            </span>
            <div>
              <h2>Tạo kho tri thức mới</h2>
              <p class="muted small">
                Gắn kho này vào Agent để kích hoạt công cụ tra cứu
                <code>kb.search</code> có trích dẫn nguồn.
              </p>
            </div>
          </div>
          <button
            mat-icon-button
            type="button"
            aria-label="Đóng biểu mẫu"
            (click)="creating.set(false)"
          >
            <mat-icon svgIcon="close" />
          </button>
        </div>

        <div class="form-grid">
          <mat-form-field appearance="outline">
            <mat-icon matPrefix class="sm" svgIcon="menu_book" />
            <mat-label>Tên kho tri thức</mat-label>
            <input
              matInput
              name="name"
              [(ngModel)]="form.name"
              required
              maxlength="80"
              placeholder="Ví dụ: Quy định nhân sự 2026"
            />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-icon matPrefix class="sm" svgIcon="public" />
            <mat-label>Phạm vi chia sẻ</mat-label>
            <mat-select name="scope" [(ngModel)]="form.scope">
              <mat-option value="personal">Cá nhân</mat-option>
              @if (isAdmin()) {
                <mat-option value="workspace"
                  >Dùng chung (cả tổ chức)</mat-option
                >
              }
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-icon matPrefix class="sm" svgIcon="shield" />
            <mat-label>Phân loại dữ liệu</mat-label>
            <mat-select name="cls" [(ngModel)]="form.data_classification">
              <mat-option value="public">Công khai (public)</mat-option>
              <mat-option value="internal">Nội bộ (internal)</mat-option>
              <mat-option value="confidential">Mật (confidential)</mat-option>
              <mat-option value="restricted"
                >Hạn chế (chỉ model tự host)</mat-option
              >
            </mat-select>
          </mat-form-field>
        </div>

        <mat-form-field appearance="outline" class="full">
          <mat-icon matPrefix class="sm" svgIcon="edit" />
          <mat-label>Mô tả (giúp Agent Architect chọn đúng kho)</mat-label>
          <input
            matInput
            name="desc"
            [(ngModel)]="form.description"
            maxlength="1000"
            placeholder="Ví dụ: Chứa sổ tay nhân viên, chính sách nghỉ phép, bảo hiểm…"
          />
        </mat-form-field>

        <div class="form-actions">
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            (click)="creating.set(false)"
          >
            Hủy
          </button>
          <button
            mat-flat-button
            class="btn-aries"
            type="submit"
            [disabled]="!form.name.trim()"
          >
            <mat-icon svgIcon="save" /> Tạo kho tri thức
          </button>
        </div>
      </form>
    }

    @if (kbs().length === 0) {
      <div class="empty-state-card">
        <div class="empty-icon-ring" aria-hidden="true">
          <mat-icon svgIcon="menu_book" />
        </div>
        <h2>Chưa có kho tri thức nào</h2>
        <p>
          Tạo kho tri thức đầu tiên để tải lên tài liệu PDF, Word, Markdown hoặc
          CSV. AI Agent sẽ tự động tra cứu ngữ nghĩa và trích dẫn chính xác từng
          đoạn nguồn.
        </p>
        @if (canCreate() && !creating()) {
          <div class="empty-actions">
            <button
              mat-flat-button
              class="btn-aries"
              type="button"
              (click)="creating.set(true)"
            >
              <mat-icon svgIcon="add" /> Tạo kho tri thức đầu tiên
            </button>
          </div>
        }
      </div>
    } @else {
      <div class="kb-layout">
        <aside class="panel-card kb-sidebar" aria-label="Danh sách kho tri thức">
          <div class="sidebar-head">
            <h2 class="sec-title">
              <mat-icon class="sm" svgIcon="menu_book" aria-hidden="true" />
              <span>Danh sách kho</span>
            </h2>
            <ui-tag [value]="kbs().length" severity="secondary" />
          </div>

          <div class="kb-list">
            @for (kb of kbs(); track kb.id) {
              <button
                type="button"
                class="kb-item"
                [class.active]="kb.id === selectedId()"
                (click)="select(kb)"
              >
                <span class="kb-item-icon" aria-hidden="true">
                  <mat-icon class="sm" svgIcon="menu_book" />
                </span>
                <div class="kb-item-body">
                  <span class="kb-name">{{ kb.name }}</span>
                  <div class="kb-item-meta">
                    <span class="muted small">{{ kb.doc_count }} tài liệu</span>
                    <span class="dot" aria-hidden="true">·</span>
                    <span class="muted small">{{
                      kb.scope === 'workspace' ? 'Dùng chung' : 'Cá nhân'
                    }}</span>
                  </div>
                </div>
              </button>
            }
          </div>
        </aside>

        @if (selected(); as kb) {
          <div class="kb-main">
            <!-- Thông tin kho & hành động -->
            <section class="panel-card kb-header-card">
              <div class="kb-header-top">
                <div class="kb-header-info">
                  <span class="icon-badge accent" aria-hidden="true">
                    <mat-icon class="sm" svgIcon="menu_book" />
                  </span>
                  <div class="kb-title-col">
                    <div class="kb-title-row">
                      <h2>{{ kb.name }}</h2>
                      <ui-tag
                        [value]="
                          kb.scope === 'workspace' ? 'Dùng chung' : 'Cá nhân'
                        "
                        severity="secondary"
                      />
                      <ui-tag
                        [value]="clsLabel(kb.data_classification)[0]"
                        [severity]="clsLabel(kb.data_classification)[1]"
                      />
                    </div>
                    @if (kb.description) {
                      <p class="muted small kb-desc">{{ kb.description }}</p>
                    } @else {
                      <p class="muted small kb-desc">
                        Chưa có mô tả cho kho tri thức này.
                      </p>
                    }
                  </div>
                </div>

                @if (kb.can_manage) {
                  <div class="kb-header-actions">
                    <label class="upload-btn btn-aries" [class.busy]="uploading()">
                      <input
                        type="file"
                        multiple
                        [accept]="accept"
                        (change)="upload($event)"
                        hidden
                      />
                      <mat-icon class="sm" svgIcon="upload_file" />
                      <span>{{
                        uploading() ? 'Đang tải lên…' : 'Tải tài liệu'
                      }}</span>
                    </label>
                    <button
                      mat-stroked-button
                      type="button"
                      class="pill-btn danger-btn"
                      (click)="removeKb(kb)"
                    >
                      <mat-icon svgIcon="delete" /> Xóa kho
                    </button>
                  </div>
                }
              </div>
            </section>

            <!-- Danh sách tài liệu -->
            <section class="panel-card docs-card">
              <div class="card-head">
                <div>
                  <h3 class="sec-title">
                    <mat-icon
                      class="sm"
                      svgIcon="description"
                      aria-hidden="true"
                    />
                    <span>Tài liệu trong kho</span>
                  </h3>
                  <p class="muted small card-sub">
                    Hỗ trợ PDF, DOCX, Markdown, TXT, HTML, CSV/TSV.
                  </p>
                </div>
                <div class="card-head-right">
                  @if (indexingCount() > 0) {
                    <ui-tag
                      [value]="'Đang lập chỉ mục ' + indexingCount() + ' tệp…'"
                      severity="warn"
                    />
                  }
                  <ui-tag
                    [value]="docs().length + ' tài liệu'"
                    severity="secondary"
                  />
                </div>
              </div>

              @if (docs().length === 0) {
                <div class="docs-empty">
                  <div class="docs-empty-icon" aria-hidden="true">
                    <mat-icon svgIcon="upload_file" />
                  </div>
                  <strong>Kho tri thức chưa có tài liệu nào</strong>
                  <p class="muted small">
                    Tải tài liệu lên để hệ thống tự động tách đoạn và lập chỉ
                    mục phục vụ tìm kiếm ngữ nghĩa.
                  </p>
                  @if (kb.can_manage) {
                    <label
                      class="upload-btn btn-aries"
                      [class.busy]="uploading()"
                    >
                      <input
                        type="file"
                        multiple
                        [accept]="accept"
                        (change)="upload($event)"
                        hidden
                      />
                      <mat-icon class="sm" svgIcon="upload_file" />
                      <span>Chọn tệp để tải lên</span>
                    </label>
                  }
                </div>
              } @else {
                <div class="table-wrap">
                  <table class="aries-table">
                    <thead>
                      <tr>
                        <th>Tài liệu</th>
                        <th>Trạng thái</th>
                        <th class="num">Đoạn</th>
                        <th class="num">Kích thước</th>
                        <th class="act"></th>
                      </tr>
                    </thead>
                    <tbody>
                      @for (d of docs(); track d.id) {
                        <tr>
                          <td>
                            <div class="doc-cell">
                              <span class="doc-icon" aria-hidden="true">
                                <mat-icon class="sm" svgIcon="description" />
                              </span>
                              <div class="doc-info">
                                <strong>{{ d.title || d.filename }}</strong>
                                <span class="muted small"
                                  >{{ d.filename }} ·
                                  {{ d.created_at | date: 'dd/MM/yyyy HH:mm' }}</span
                                >
                              </div>
                            </div>
                          </td>
                          <td>
                            <ui-tag
                              [value]="status(d)[0]"
                              [severity]="status(d)[1]"
                            />
                            @if (d.error) {
                              <div class="doc-err small">{{ d.error }}</div>
                            }
                          </td>
                          <td class="num">
                            <span class="flow-chip chip-read">{{
                              d.chunk_count
                            }}</span>
                          </td>
                          <td class="num muted small">
                            {{ d.size_bytes / 1024 | number: '1.0-0' }} KB
                          </td>
                          <td class="act">
                            @if (kb.can_manage) {
                              @if (d.status === 'failed') {
                                <button
                                  mat-stroked-button
                                  class="pill-btn retry-btn"
                                  type="button"
                                  (click)="reingest(d)"
                                >
                                  <mat-icon svgIcon="refresh" /> Thử lại
                                </button>
                              }
                              <button
                                mat-icon-button
                                type="button"
                                class="del-icon-btn"
                                [attr.aria-label]="'Xóa ' + d.filename"
                                title="Xóa tài liệu"
                                (click)="removeDoc(d)"
                              >
                                <mat-icon svgIcon="delete" />
                              </button>
                            }
                          </td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
              }
            </section>

            <!-- Kiểm thử tìm kiếm -->
            <section class="panel-card search-card">
              <div class="card-head">
                <div>
                  <h3 class="sec-title">
                    <mat-icon class="sm" svgIcon="search" aria-hidden="true" />
                    <span>Kiểm thử truy vấn tri thức</span>
                  </h3>
                  <p class="muted small card-sub">
                    Thử đặt câu hỏi giống như cách Agent gọi
                    <code>kb.search</code> để kiểm tra độ chính xác của các đoạn
                    trích dẫn.
                  </p>
                </div>
              </div>

              <form class="search-form" (ngSubmit)="search()">
                <mat-form-field
                  appearance="outline"
                  subscriptSizing="dynamic"
                  class="grow"
                >
                  <mat-icon matPrefix class="sm" svgIcon="search" />
                  <mat-label>Nhập câu hỏi kiểm thử…</mat-label>
                  <input
                    matInput
                    name="q"
                    [(ngModel)]="query"
                    placeholder="Ví dụ: Nhân viên được nghỉ phép bao nhiêu ngày một năm?"
                  />
                </mat-form-field>
                <button
                  mat-flat-button
                  class="btn-aries search-btn"
                  type="submit"
                  [disabled]="query.trim().length < 2"
                >
                  <mat-icon svgIcon="search" /> Tìm kiếm
                </button>
              </form>

              @if (result(); as r) {
                <div class="results-wrap">
                  @if (r.citations.length === 0) {
                    <div class="no-hits muted small">
                      <mat-icon class="sm" svgIcon="search" />
                      <span>Không tìm thấy đoạn tài liệu nào liên quan.</span>
                    </div>
                  } @else {
                    <div class="hits-grid">
                      @for (c of r.citations; track c.n) {
                        <div class="hit-card">
                          <span class="hit-num">[{{ c.n }}]</span>
                          <div class="hit-body">
                            <strong>{{ c.title }}</strong>
                            <span class="muted small">
                              @if (c.page) {
                                Trang {{ c.page }}
                              }
                              @if (c.heading) {
                                {{ c.page ? ' · ' : '' }}{{ c.heading }}
                              }
                            </span>
                          </div>
                          <span class="flow-chip chip-write">
                            điểm {{ r.scores[$index] | number: '1.2-3' }}
                          </span>
                        </div>
                      }
                    </div>
                    <div class="raw-box">
                      <span class="raw-label">Ngữ cảnh trả về cho Agent</span>
                      <pre class="raw">{{ r.text }}</pre>
                    </div>
                  }
                </div>
              }
            </section>
          </div>
        }
      </div>
    }
  `,
  styles: `
    /* Create form panel */
    .create-panel {
      margin-bottom: 1.15rem;
      border-color: var(--app-border-strong);
      box-shadow: var(--app-shadow-md);
    }
    .panel-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 1rem;
      h2 {
        margin: 0;
        font-size: 1.05rem;
      }
      p {
        margin: 0.15rem 0 0;
      }
    }
    .panel-head-left {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 0.25rem 0.9rem;
    }
    .form-actions {
      display: flex;
      justify-content: flex-end;
      gap: 0.55rem;
      margin-top: 0.25rem;
    }

    /* Master-detail layout */
    .kb-layout {
      display: grid;
      grid-template-columns: 285px minmax(0, 1fr);
      gap: 1.1rem;
      align-items: start;
    }
    @media (max-width: 900px) {
      .kb-layout {
        grid-template-columns: 1fr;
      }
    }

    .kb-sidebar {
      padding: 0.9rem;
      position: sticky;
      top: 1rem;
    }
    .sidebar-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      padding: 0.15rem 0.25rem 0.75rem;
      margin-bottom: 0.55rem;
      border-bottom: 1px solid var(--app-border);
      h2 {
        margin: 0;
        font-size: 0.96rem;
      }
    }
    .kb-list {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      max-height: 68vh;
      overflow-y: auto;
    }
    .kb-item {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      width: 100%;
      text-align: left;
      padding: 0.65rem 0.75rem;
      border-radius: var(--app-radius-md);
      border: 1px solid transparent;
      background: transparent;
      color: var(--app-text);
      font: inherit;
      cursor: pointer;
      transition: all 0.16s ease;
      &:hover {
        background: var(--app-surface-2);
        border-color: var(--app-border);
      }
      &.active {
        background: var(--app-highlight);
        border-color: rgba(255, 61, 139, 0.38);
        box-shadow: var(--app-shadow-sm);
        .kb-item-icon {
          background: var(--aries-gradient);
          color: #fff;
          border-color: transparent;
        }
        .kb-name {
          font-weight: 700;
        }
      }
    }
    .kb-item-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      color: var(--app-primary);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .kb-item-body {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1;
    }
    .kb-name {
      font-size: 0.9rem;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .kb-item-meta {
      display: flex;
      align-items: center;
      gap: 0.3rem;
      .dot {
        color: var(--app-muted);
      }
    }

    /* Right column */
    .kb-main {
      display: flex;
      flex-direction: column;
      gap: 1rem;
      min-width: 0;
    }
    .kb-header-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .kb-header-info {
      display: flex;
      align-items: flex-start;
      gap: 0.85rem;
      min-width: 0;
      flex: 1;
    }
    .kb-title-col {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      min-width: 0;
    }
    .kb-title-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      h2 {
        margin: 0;
        font-size: 1.15rem;
      }
    }
    .kb-desc {
      margin: 0;
    }
    .kb-header-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      flex-shrink: 0;
    }

    .upload-btn {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      cursor: pointer;
      padding: 0.52rem 1.05rem;
      font-size: 0.875rem;
      font-weight: 600;
      line-height: 1.25;
      text-decoration: none;
      transition: filter 0.16s ease;
      &:hover {
        filter: brightness(1.06);
      }
      &.busy {
        opacity: 0.65;
        pointer-events: none;
      }
    }

    .card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 0.9rem;
      h3 {
        margin: 0;
        font-size: 0.98rem;
      }
    }
    .card-sub {
      margin: 0.18rem 0 0;
    }
    .card-head-right {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }

    /* Empty docs dropzone */
    .docs-empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 0.45rem;
      padding: 2rem 1.25rem;
      border-radius: var(--app-radius-lg);
      border: 1.5px dashed var(--app-border-strong);
      background: var(--app-surface-2);
      p {
        margin: 0 0 0.4rem;
        max-width: 30rem;
      }
    }
    .docs-empty-icon {
      width: 48px;
      height: 48px;
      border-radius: 14px;
      background: var(--app-highlight);
      color: var(--app-primary);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 0.2rem;
    }

    .doc-cell {
      display: flex;
      align-items: center;
      gap: 0.65rem;
    }
    .doc-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      color: var(--app-primary);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .doc-info {
      display: flex;
      flex-direction: column;
      min-width: 0;
      strong {
        font-size: 0.88rem;
      }
    }
    .doc-err {
      color: var(--app-danger);
      margin-top: 0.25rem;
    }
    .retry-btn {
      margin-right: 0.25rem;
    }

    /* Search playground */
    .search-form {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      flex-wrap: wrap;
    }
    .grow {
      flex: 1;
      min-width: 220px;
    }
    .search-btn {
      min-height: 44px;
    }
    .results-wrap {
      margin-top: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .no-hits {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.85rem 1rem;
      border-radius: var(--app-radius-md);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
    }
    .hits-grid {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }
    .hit-card {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      padding: 0.65rem 0.85rem;
      border-radius: var(--app-radius-md);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
    }
    .hit-num {
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--app-primary);
      background: var(--app-highlight);
      border: 1px solid rgba(255, 61, 139, 0.25);
      padding: 0.15rem 0.45rem;
      border-radius: 6px;
      flex-shrink: 0;
    }
    .hit-body {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-width: 0;
      strong {
        font-size: 0.88rem;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }
    .raw-box {
      border-radius: var(--app-radius-md);
      border: 1px solid var(--app-border);
      background: var(--app-surface-2);
      overflow: hidden;
    }
    .raw-label {
      display: block;
      padding: 0.45rem 0.85rem;
      font-size: 0.73rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--app-muted);
      border-bottom: 1px solid var(--app-border);
      background: var(--app-surface);
    }
    .raw {
      margin: 0;
      padding: 0.8rem 0.85rem;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 0.78rem;
      line-height: 1.5;
      max-height: 20rem;
      overflow: auto;
      color: var(--app-text);
    }
  `,
})
export class Knowledge implements OnInit {
  private readonly api = inject(KnowledgeApi);
  private readonly session = inject(SessionService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly accept = ACCEPT;
  protected readonly kbs = signal<KnowledgeBaseInfo[]>([]);
  protected readonly docs = signal<KnowledgeDocument[]>([]);
  protected readonly selectedId = signal<string | null>(null);
  protected readonly creating = signal(false);
  protected readonly uploading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly result = signal<KnowledgeSearchResult | null>(null);
  protected query = '';
  protected form = {
    name: '',
    description: '',
    scope: 'personal' as 'personal' | 'workspace',
    data_classification: 'internal' as DataClassification,
  };

  protected readonly selected = computed(
    () => this.kbs().find((k) => k.id === this.selectedId()) ?? null,
  );
  protected readonly totalDocs = computed(() =>
    this.kbs().reduce((sum, k) => sum + (k.doc_count || 0), 0),
  );
  protected readonly workspaceCount = computed(
    () => this.kbs().filter((k) => k.scope === 'workspace').length,
  );
  protected readonly indexingCount = computed(
    () =>
      this.docs().filter(
        (d) => d.status === 'pending' || d.status === 'processing',
      ).length,
  );
  protected readonly canCreate = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('builder', 'admin');
  });
  protected readonly isAdmin = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin');
  });

  private poll: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.reload();
    this.destroyRef.onDestroy(() => this.stopPoll());
  }

  protected refreshAll(): void {
    this.error.set(null);
    this.reload(this.selectedId());
  }

  protected status(d: KnowledgeDocument): [string, Severity] {
    return STATUS[d.status];
  }

  protected clsLabel(c: DataClassification): [string, Severity] {
    return CLS_LABEL[c] ?? [c, 'info'];
  }

  protected select(kb: KnowledgeBaseInfo): void {
    this.selectedId.set(kb.id);
    this.result.set(null);
    this.loadDocs();
  }

  protected create(): void {
    this.api.create(this.form).subscribe({
      next: (kb) => {
        this.creating.set(false);
        this.form = { ...this.form, name: '', description: '' };
        this.reload(kb.id);
      },
      error: (e) => this.fail(e),
    });
  }

  protected upload(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    const kbId = this.selectedId();
    input.value = '';
    if (!files.length || !kbId) return;
    this.uploading.set(true);
    this.error.set(null);
    let left = files.length;
    for (const file of files) {
      this.api.upload(kbId, file).subscribe({
        next: () => this.afterUpload(--left),
        error: (e) => {
          this.fail(e, file.name);
          this.afterUpload(--left);
        },
      });
    }
  }

  protected reingest(d: KnowledgeDocument): void {
    this.api
      .reingest(d.id)
      .subscribe({ next: () => this.loadDocs(), error: (e) => this.fail(e) });
  }

  protected removeDoc(d: KnowledgeDocument): void {
    if (!confirm(`Xóa tài liệu “${d.title || d.filename}”?`)) return;
    this.api.removeDocument(d.id).subscribe({
      next: () => this.reload(this.selectedId()),
      error: (e) => this.fail(e),
    });
  }

  protected removeKb(kb: KnowledgeBaseInfo): void {
    if (
      !confirm(
        `Xóa kho “${kb.name}” cùng toàn bộ tài liệu? Agent đang dùng sẽ mất nguồn này.`,
      )
    ) {
      return;
    }
    this.api.remove(kb.id).subscribe({
      next: () => {
        this.selectedId.set(null);
        this.reload();
      },
      error: (e) => this.fail(e),
    });
  }

  protected search(): void {
    const kbId = this.selectedId();
    if (!kbId) return;
    this.api.search(kbId, this.query.trim()).subscribe({
      next: (r) => this.result.set(r),
      error: (e) => this.fail(e),
    });
  }

  private afterUpload(left: number): void {
    if (left > 0) return;
    this.uploading.set(false);
    this.loadDocs();
  }

  private reload(selectId?: string | null): void {
    this.api.list().subscribe({
      next: (list) => {
        this.kbs.set(list);
        const keep = selectId ?? this.selectedId() ?? list[0]?.id ?? null;
        this.selectedId.set(
          list.some((k) => k.id === keep) ? keep : (list[0]?.id ?? null),
        );
        this.loadDocs();
      },
      error: (e) => this.fail(e),
    });
  }

  private loadDocs(): void {
    const kbId = this.selectedId();
    if (!kbId) {
      this.docs.set([]);
      return;
    }
    this.api.documents(kbId).subscribe({
      next: (docs) => {
        this.docs.set(docs);
        // Tài liệu đang xử lý ở worker → tự làm mới trạng thái.
        const busy = docs.some(
          (d) => d.status === 'pending' || d.status === 'processing',
        );
        if (busy) this.startPoll();
        else {
          this.stopPoll();
          this.api.list().subscribe((list) => this.kbs.set(list));
        }
      },
      error: (e) => this.fail(e),
    });
  }

  private startPoll(): void {
    if (this.poll) return;
    this.poll = setInterval(() => this.loadDocs(), 3000);
  }

  private stopPoll(): void {
    if (this.poll) clearInterval(this.poll);
    this.poll = null;
  }

  private fail(
    err: { error?: { error?: { message?: string } } },
    file?: string,
  ): void {
    const msg = err.error?.error?.message ?? 'Thao tác thất bại.';
    this.error.set(file ? `${file}: ${msg}` : msg);
  }
}

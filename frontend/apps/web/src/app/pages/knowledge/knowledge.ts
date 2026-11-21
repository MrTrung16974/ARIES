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
  pending: ['chờ xử lý', 'secondary'],
  processing: ['đang lập chỉ mục', 'warn'],
  ready: ['sẵn sàng', 'success'],
  failed: ['lỗi', 'danger'],
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
    <div class="section-head">
      <div>
        <h1>Kho tri thức</h1>
        <p class="muted subtitle">
          Tải tài liệu nội bộ (PDF, Word, Markdown, HTML, CSV…) để agent tra cứu và trích dẫn
          nguồn. Tài liệu được xử lý ngay trên máy chủ của bạn, không gửi ra ngoài.
        </p>
      </div>
      @if (canCreate() && !creating()) {
        <button mat-flat-button class="btn-aries" type="button" (click)="creating.set(true)">
          <mat-icon svgIcon="add" /> Tạo kho tri thức
        </button>
      }
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }

    @if (creating()) {
      <form class="panel" (ngSubmit)="create()">
        <div class="grid">
          <mat-form-field appearance="outline">
            <mat-label>Tên</mat-label>
            <input matInput name="name" [(ngModel)]="form.name" required maxlength="80" />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Phạm vi</mat-label>
            <mat-select name="scope" [(ngModel)]="form.scope">
              <mat-option value="personal">Cá nhân</mat-option>
              @if (isAdmin()) {
                <mat-option value="workspace">Dùng chung (cả tổ chức)</mat-option>
              }
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Phân loại dữ liệu</mat-label>
            <mat-select name="cls" [(ngModel)]="form.data_classification">
              <mat-option value="public">Công khai</mat-option>
              <mat-option value="internal">Nội bộ</mat-option>
              <mat-option value="confidential">Mật</mat-option>
              <mat-option value="restricted">Hạn chế (chỉ model tự host)</mat-option>
            </mat-select>
          </mat-form-field>
        </div>
        <mat-form-field appearance="outline" class="full">
          <mat-label>Mô tả (giúp Architect chọn đúng kho)</mat-label>
          <input matInput name="desc" [(ngModel)]="form.description" maxlength="1000" />
        </mat-form-field>
        <div class="actions">
          <button mat-flat-button class="btn-aries" type="submit">
            <mat-icon svgIcon="save" /> Tạo
          </button>
          <button mat-stroked-button type="button" (click)="creating.set(false)">Hủy</button>
        </div>
      </form>
    }

    <div class="layout">
      <aside class="kb-list">
        @for (kb of kbs(); track kb.id) {
          <button
            type="button"
            class="kb-item"
            [class.active]="kb.id === selectedId()"
            (click)="select(kb)"
          >
            <mat-icon class="sm" svgIcon="menu_book" aria-hidden="true" />
            <span class="kb-name">{{ kb.name }}</span>
            <span class="muted small">{{ kb.doc_count }} tài liệu</span>
          </button>
        } @empty {
          <p class="muted small empty">Chưa có kho tri thức nào.</p>
        }
      </aside>

      @if (selected(); as kb) {
        <section class="detail">
          <div class="title-line">
            <h2>{{ kb.name }}</h2>
            <ui-tag [value]="kb.scope === 'workspace' ? 'dùng chung' : 'cá nhân'" severity="secondary" />
            <ui-tag [value]="kb.data_classification" severity="info" />
            <span class="spacer"></span>
            @if (kb.can_manage) {
              <label class="upload" [class.busy]="uploading()">
                <input type="file" multiple [accept]="accept" (change)="upload($event)" hidden />
                <mat-icon svgIcon="upload_file" /> Tải tài liệu
              </label>
              <button mat-stroked-button type="button" class="danger" (click)="removeKb(kb)">
                <mat-icon svgIcon="delete" /> Xóa kho
              </button>
            }
          </div>
          @if (kb.description) {
            <p class="muted small">{{ kb.description }}</p>
          }

          <table class="docs">
            <thead>
              <tr><th>Tài liệu</th><th>Trạng thái</th><th>Đoạn</th><th>Kích thước</th><th></th></tr>
            </thead>
            <tbody>
              @for (d of docs(); track d.id) {
                <tr>
                  <td>
                    <mat-icon class="sm" svgIcon="description" aria-hidden="true" />
                    <strong>{{ d.title || d.filename }}</strong>
                    <div class="muted small">{{ d.filename }} · {{ d.created_at | date: 'short' }}</div>
                  </td>
                  <td>
                    <ui-tag [value]="status(d)[0]" [severity]="status(d)[1]" />
                    @if (d.error) {
                      <div class="err small">{{ d.error }}</div>
                    }
                  </td>
                  <td>{{ d.chunk_count }}</td>
                  <td class="muted small">{{ d.size_bytes / 1024 | number: '1.0-0' }} KB</td>
                  <td class="row-actions">
                    @if (kb.can_manage) {
                      @if (d.status === 'failed') {
                        <button mat-button type="button" (click)="reingest(d)">Thử lại</button>
                      }
                      <button
                        mat-icon-button
                        type="button"
                        [attr.aria-label]="'Xóa ' + d.filename"
                        (click)="removeDoc(d)"
                      >
                        <mat-icon svgIcon="delete" />
                      </button>
                    }
                  </td>
                </tr>
              } @empty {
                <tr><td colspan="5" class="muted">Chưa có tài liệu.</td></tr>
              }
            </tbody>
          </table>

          <form class="search" (ngSubmit)="search()">
            <mat-form-field appearance="outline" subscriptSizing="dynamic" class="grow">
              <mat-icon matPrefix class="sm" svgIcon="search" />
              <mat-label>Thử tìm kiếm trong kho</mat-label>
              <input matInput name="q" [(ngModel)]="query" placeholder="Ví dụ: được nghỉ phép bao nhiêu ngày?" />
            </mat-form-field>
            <button mat-stroked-button type="submit" [disabled]="query.trim().length < 2">Tìm</button>
          </form>
          @if (result(); as r) {
            <div class="results">
              @for (c of r.citations; track c.n) {
                <div class="hit">
                  <strong>[{{ c.n }}] {{ c.title }}</strong>
                  <span class="muted small">
                    @if (c.page) { trang {{ c.page }} }
                    @if (c.heading) { · {{ c.heading }} }
                    · điểm {{ r.scores[$index] }}
                  </span>
                </div>
              } @empty {
                <p class="muted small">Không tìm thấy đoạn nào liên quan.</p>
              }
              @if (r.citations.length) {
                <pre class="raw">{{ r.text }}</pre>
              }
            </div>
          }
        </section>
      }
    </div>
  `,
  styles: `
    .section-head {
      display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;
      margin-bottom: 0.85rem;
      h1 { font-size: 1.25rem; margin: 0 0 0.15rem; }
      .subtitle { margin: 0; font-size: 0.88rem; max-width: 60rem; }
    }
    .panel, .detail, .kb-list {
      background: var(--app-surface); border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl); box-shadow: var(--app-shadow-sm); padding: 1rem 1.1rem;
    }
    .panel { margin-bottom: 1rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0 0.9rem; }
    .full { width: 100%; }
    .actions { display: flex; gap: 0.5rem; }
    .layout { display: grid; grid-template-columns: 260px 1fr; gap: 1rem; align-items: start; }
    @media (max-width: 860px) { .layout { grid-template-columns: 1fr; } }
    .kb-list { display: flex; flex-direction: column; gap: 0.3rem; padding: 0.6rem; }
    .kb-item {
      display: grid; grid-template-columns: auto 1fr; gap: 0 0.45rem; text-align: left;
      background: none; border: 1px solid transparent; border-radius: var(--app-radius-md);
      padding: 0.5rem 0.6rem; cursor: pointer; font: inherit; color: var(--app-text);
      .muted { grid-column: 2; }
      &.active { border-color: var(--app-border); background: var(--app-surface-2); font-weight: 600; }
    }
    .kb-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty { padding: 0.5rem; }
    .title-line { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
      h2 { font-size: 1.1rem; margin: 0; } }
    .spacer { flex: 1; }
    .upload {
      display: inline-flex; align-items: center; gap: 0.35rem; cursor: pointer; padding: 0.4rem 0.9rem;
      border-radius: 999px; background: var(--app-primary, #e11d74); color: #fff; font-weight: 600;
      font-size: 0.88rem;
      &.busy { opacity: 0.6; pointer-events: none; }
    }
    table.docs { width: 100%; border-collapse: collapse; margin: 0.8rem 0; font-size: 0.88rem; }
    table.docs th, table.docs td {
      text-align: left; padding: 0.45rem; border-bottom: 1px solid var(--app-border); vertical-align: top;
    }
    .row-actions { white-space: nowrap; text-align: right; }
    .err { color: var(--app-danger, #dc2626); margin-top: 0.2rem; }
    .danger { color: var(--app-danger, #dc2626); }
    .search { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.5rem; }
    .grow { flex: 1; }
    .results { margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.3rem; }
    .raw {
      white-space: pre-wrap; font-size: 0.8rem; background: var(--app-surface-2); padding: 0.6rem;
      border-radius: 8px; max-height: 20rem; overflow: auto;
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

  protected status(d: KnowledgeDocument): [string, Severity] {
    return STATUS[d.status];
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
    this.api.reingest(d.id).subscribe({ next: () => this.loadDocs(), error: (e) => this.fail(e) });
  }

  protected removeDoc(d: KnowledgeDocument): void {
    if (!confirm(`Xóa tài liệu “${d.title || d.filename}”?`)) return;
    this.api.removeDocument(d.id).subscribe({
      next: () => this.reload(this.selectedId()),
      error: (e) => this.fail(e),
    });
  }

  protected removeKb(kb: KnowledgeBaseInfo): void {
    if (!confirm(`Xóa kho “${kb.name}” cùng toàn bộ tài liệu? Agent đang dùng sẽ mất nguồn này.`)) {
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
        this.selectedId.set(list.some((k) => k.id === keep) ? keep : (list[0]?.id ?? null));
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
        const busy = docs.some((d) => d.status === 'pending' || d.status === 'processing');
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

  private fail(err: { error?: { error?: { message?: string } } }, file?: string): void {
    const msg = err.error?.error?.message ?? 'Thao tác thất bại.';
    this.error.set(file ? `${file}: ${msg}` : msg);
  }
}

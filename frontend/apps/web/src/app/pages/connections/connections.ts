import { DatePipe } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import {
  ConnectionInfo,
  ConnectionScope,
  ConnectionsApi,
  ConnectorInfo,
  JsonSchema,
  OAuthApi,
  OAuthProviderInfo,
  SessionService,
} from '@aiagent/core';
import { Notice, Tag } from '@aiagent/ui';

interface FieldDef {
  key: string;
  label: string;
  kind: 'text' | 'number' | 'boolean' | 'select' | 'list' | 'textarea';
  required: boolean;
  options: string[];
  hint: string;
  placeholder: string;
}

/** Sinh trường form từ config_schema của connector (chỉ các kiểu đơn giản). */
export function fieldsOf(schema: JsonSchema): FieldDef[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([key, p]) => {
    let kind: FieldDef['kind'] = 'text';
    if (p.enum) kind = 'select';
    else if (p.type === 'string' && p.contentMediaType) kind = 'textarea';
    else if (p.type === 'integer' || p.type === 'number') kind = 'number';
    else if (p.type === 'boolean') kind = 'boolean';
    else if (p.type === 'array') kind = 'list';
    return {
      key,
      label: p.title ?? key,
      kind,
      required: required.has(key),
      options: (p.enum ?? []).map(String),
      placeholder:
        p.contentMediaType === 'application/x-pem-file'
          ? '-----BEGIN CERTIFICATE-----'
          : p.contentMediaType === 'text/x-protobuf'
            ? 'syntax = "proto3";'
            : '',
      hint: kind === 'list' ? 'Phân tách bằng dấu phẩy' : (p.description ?? ''),
    };
  });
}

/** Giá trị form → config đúng kiểu (bỏ trường trống không bắt buộc). */
export function toConfig(
  fields: FieldDef[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.key];
    if (f.kind === 'boolean') {
      out[f.key] = !!v;
    } else if (f.kind === 'number') {
      if (v !== '' && v !== null && v !== undefined) out[f.key] = Number(v);
    } else if (f.kind === 'list') {
      const items = String(v ?? '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      if (items.length) out[f.key] = items;
    } else if (v !== '' && v !== null && v !== undefined) {
      out[f.key] = String(v);
    }
  }
  return out;
}

function initialValues(
  schema: JsonSchema,
  config: Record<string, unknown> = {},
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const [key, p] of Object.entries(schema.properties ?? {})) {
    const v = key in config ? config[key] : p.default;
    values[key] = Array.isArray(v) ? v.join(', ') : (v ?? (p.type === 'boolean' ? false : ''));
  }
  return values;
}

@Component({
  selector: 'app-connections',
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
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
          <mat-icon class="sm" svgIcon="cable" />
        </span>
        <div class="hero-copy">
          <h1>Kết nối dữ liệu</h1>
          <p class="subtitle">
            Kết nối tới cơ sở dữ liệu và dịch vụ nội bộ để gắn vào AI Agent.
            Thông tin xác thực được mã hóa an toàn trong kho bí mật OpenBao và
            không bao giờ hiển thị lại.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        @if (canCreate() && !editing()) {
          <button
            mat-flat-button
            class="btn-aries"
            type="button"
            (click)="startCreate()"
          >
            <mat-icon svgIcon="add" /> Thêm kết nối
          </button>
        }
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon" aria-hidden="true">
          <mat-icon class="sm" svgIcon="cable" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ connections().length }}</span>
          <span class="kpi-label">Tổng kết nối</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="check_circle" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ activeCount() }}</span>
          <span class="kpi-label">Đang hoạt động</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="database" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ workspaceCount() }}</span>
          <span class="kpi-label">Dùng chung tổ chức</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon warn" aria-hidden="true">
          <mat-icon class="sm" svgIcon="key" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">OpenBao</span>
          <span class="kpi-label">Kho bí mật mã hóa</span>
        </div>
      </div>
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }
    @if (info()) {
      <ui-notice severity="success">{{ info() }}</ui-notice>
    }

    @if (editing(); as e) {
      <form class="panel" (ngSubmit)="save()">
        <div class="panel-head">
          <div class="panel-title">
            <span class="icon-badge" aria-hidden="true">
              <mat-icon class="sm" [svgIcon]="e.id ? 'edit' : 'add'" />
            </span>
            <div>
              <h2>{{ e.id ? 'Chỉnh sửa kết nối' : 'Tạo kết nối mới' }}</h2>
              <p class="muted small">
                Cấu hình thông số và xác thực để kiểm tra kết nối ngay sau khi
                lưu.
              </p>
            </div>
          </div>
          <button
            mat-icon-button
            type="button"
            aria-label="Đóng biểu mẫu"
            (click)="editing.set(null)"
          >
            <mat-icon svgIcon="close" />
          </button>
        </div>

        <div class="grid">
          <mat-form-field appearance="outline">
            <mat-label>Tên kết nối</mat-label>
            <input
              matInput
              name="name"
              [(ngModel)]="e.name"
              placeholder="Ví dụ: PostgreSQL Bán hàng"
              required
              maxlength="80"
            />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Loại connector</mat-label>
            <mat-select
              name="connector"
              [(ngModel)]="e.connectorId"
              (ngModelChange)="pickConnector($event)"
              [disabled]="!!e.id"
            >
              @for (c of connectors(); track c.id) {
                <mat-option [value]="c.id">{{ c.name }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
          @if (!e.id) {
            <mat-form-field appearance="outline">
              <mat-label>Phạm vi chia sẻ</mat-label>
              <mat-select name="scope" [(ngModel)]="e.scope">
                <mat-option value="personal">Cá nhân (chỉ mình tôi)</mat-option>
                @if (isAdmin()) {
                  <mat-option value="workspace"
                    >Dùng chung (cả tổ chức)</mat-option
                  >
                }
              </mat-select>
            </mat-form-field>
          }
        </div>

        @if (connector(); as c) {
          <p class="muted small conn-desc">{{ c.description }}</p>
          @if (c.oauth_provider && !e.id) {
            <div class="oauth-box">
              @if (providerName(c.oauth_provider); as pn) {
                <p class="small">
                  Kết nối bằng tài khoản <strong>{{ pn }}</strong> (OAuth). Bạn
                  sẽ được chuyển sang trang đăng nhập của {{ pn }}; ARIES chỉ
                  lưu token trong kho bí mật, không thấy mật khẩu.
                </p>
                <button
                  mat-flat-button
                  class="btn-aries"
                  type="button"
                  [disabled]="saving() || !e.name.trim()"
                  (click)="startOAuth()"
                >
                  <mat-icon svgIcon="login" /> Kết nối với {{ pn }}
                </button>
              } @else {
                <ui-notice severity="warn" [compact]="true">
                  Chưa cấu hình nhà cung cấp OAuth “{{ c.oauth_provider }}” —
                  liên hệ quản trị.
                </ui-notice>
              }
            </div>
          }
          <div class="grid">
            @for (f of fields(); track f.key) {
              @switch (f.kind) {
                @case ('boolean') {
                  <mat-checkbox [name]="f.key" [(ngModel)]="e.values[f.key]">
                    {{ f.label }}
                  </mat-checkbox>
                }
                @case ('select') {
                  <mat-form-field appearance="outline">
                    <mat-label>{{ f.label }}</mat-label>
                    <mat-select [name]="f.key" [(ngModel)]="e.values[f.key]">
                      @for (o of f.options; track o) {
                        <mat-option [value]="o">{{ o }}</mat-option>
                      }
                    </mat-select>
                  </mat-form-field>
                }
                @case ('textarea') {
                  <mat-form-field appearance="outline" class="wide">
                    <mat-label>{{ f.label }}</mat-label>
                    <textarea
                      matInput
                      class="pem"
                      rows="4"
                      spellcheck="false"
                      [name]="f.key"
                      [(ngModel)]="e.values[f.key]"
                      [required]="f.required"
                      [placeholder]="f.placeholder"
                    ></textarea>
                    @if (f.hint) {
                      <mat-hint>{{ f.hint }}</mat-hint>
                    }
                  </mat-form-field>
                }
                @default {
                  <mat-form-field appearance="outline">
                    <mat-label>{{ f.label }}</mat-label>
                    <input
                      matInput
                      [name]="f.key"
                      [type]="f.kind === 'number' ? 'number' : 'text'"
                      [(ngModel)]="e.values[f.key]"
                      [required]="f.required"
                    />
                    @if (f.hint) {
                      <mat-hint>{{ f.hint }}</mat-hint>
                    }
                  </mat-form-field>
                }
              }
            }
            @for (s of c.secrets; track s) {
              <mat-form-field appearance="outline" [class.wide]="isMultilineSecret(c, s)">
                <mat-label>
                  <mat-icon class="sm" svgIcon="key" aria-hidden="true" />
                  {{ s }}
                </mat-label>
                @if (isMultilineSecret(c, s)) {
                  <!-- Khóa riêng PEM: ô nhiều dòng, che chữ bằng CSS; không bao giờ hiển thị lại -->
                  <textarea
                    matInput
                    class="pem secret"
                    rows="4"
                    spellcheck="false"
                    autocomplete="off"
                    [name]="'secret_' + s"
                    [(ngModel)]="e.secrets[s]"
                    [required]="!e.id && !(c.optional_secrets ?? []).includes(s)"
                    placeholder="-----BEGIN PRIVATE KEY-----"
                  ></textarea>
                } @else {
                  <input
                    matInput
                    type="password"
                    autocomplete="new-password"
                    [name]="'secret_' + s"
                    [(ngModel)]="e.secrets[s]"
                    [required]="!e.id && !(c.optional_secrets ?? []).includes(s)"
                  />
                }
                @if (e.id) {
                  <mat-hint>Để trống = giữ nguyên giá trị đã lưu</mat-hint>
                } @else if ((c.optional_secrets ?? []).includes(s)) {
                  <mat-hint>Không bắt buộc</mat-hint>
                }
              </mat-form-field>
            }
          </div>
        }

        <div class="panel-actions">
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            (click)="editing.set(null)"
          >
            Hủy
          </button>
          @if (!(connector()?.oauth_provider && !e.id)) {
            <button
              mat-flat-button
              class="btn-aries"
              type="submit"
              [disabled]="saving()"
            >
              <mat-icon svgIcon="save" />
              {{ saving() ? 'Đang lưu…' : 'Lưu & kiểm tra' }}
            </button>
          }
        </div>
      </form>
    }

    @if (connections().length > 0) {
      <div class="conn-grid">
        @for (c of connections(); track c.id) {
          <article class="conn-card">
            <div class="card-top">
              <span class="icon-badge" aria-hidden="true">
                <mat-icon class="sm" svgIcon="database" />
              </span>
              <div class="card-body">
                <div class="title-line">
                  <strong>{{ c.name }}</strong>
                  <ui-tag
                    [value]="connectorName(c.connector_id)"
                    severity="primary"
                  />
                  <ui-tag
                    [value]="
                      c.scope === 'workspace' ? 'Dùng chung' : 'Cá nhân'
                    "
                    severity="secondary"
                  />
                  <ui-tag
                    [value]="c.status === 'active' ? 'Đang bật' : 'Đang tắt'"
                    [severity]="c.status === 'active' ? 'success' : 'warn'"
                  />
                </div>
                @if (summary(c).length) {
                  <div class="cfg">
                    @for (kv of summary(c); track kv) {
                      <span class="flow-chip chip-read">{{ kv }}</span>
                    }
                  </div>
                }
              </div>
            </div>

            @if (isDynamic(c)) {
              <div class="cfg">
                @for (t of c.tools; track t.name) {
                  <span
                    class="flow-chip"
                    [class.chip-read]="t.risk === 'read'"
                    [title]="t.description + ' · rủi ro: ' + t.risk"
                    >{{ t.name }}</span
                  >
                } @empty {
                  <span class="flow-chip">Chưa có tool — bấm “Đồng bộ tool”</span>
                }
              </div>
            }

            @if (c.last_test_at) {
              <ui-notice
                [severity]="c.last_test_ok ? 'success' : 'error'"
                [compact]="true"
              >
                {{ c.last_test_note }} ·
                {{ c.last_test_at | date: 'dd/MM HH:mm' }}
              </ui-notice>
            }

            @if (c.can_manage) {
              <div class="card-foot">
                <button
                  mat-stroked-button
                  class="pill-btn"
                  type="button"
                  (click)="test(c)"
                  [disabled]="busyId() === c.id"
                >
                  <mat-icon svgIcon="play_arrow" />
                  {{ busyId() === c.id ? 'Đang kiểm tra…' : 'Kiểm tra' }}
                </button>
                @if (isDynamic(c)) {
                  <button
                    mat-stroked-button
                    class="pill-btn"
                    type="button"
                    (click)="syncTools(c)"
                    [disabled]="busyId() === c.id"
                  >
                    <mat-icon svgIcon="sync" /> Đồng bộ tool
                  </button>
                }
                <button
                  mat-stroked-button
                  class="pill-btn"
                  type="button"
                  (click)="startEdit(c)"
                >
                  <mat-icon svgIcon="edit" /> Sửa
                </button>
                <button
                  mat-stroked-button
                  class="pill-btn"
                  type="button"
                  (click)="toggle(c)"
                >
                  {{ c.status === 'active' ? 'Tắt' : 'Bật' }}
                </button>
                <button
                  mat-stroked-button
                  class="pill-btn danger"
                  type="button"
                  (click)="remove(c)"
                >
                  <mat-icon svgIcon="delete" /> Xóa
                </button>
              </div>
            }
          </article>
        }
      </div>
    } @else if (!editing()) {
      <div class="empty-state-card">
        <div class="empty-icon-ring" aria-hidden="true">
          <mat-icon svgIcon="cable" />
        </div>
        <h2>Chưa có kết nối dữ liệu nào</h2>
        <p>
          Thêm kết nối tới PostgreSQL hoặc các hệ thống hỗ trợ OAuth để AI Agent
          có thể tra cứu và thao tác dữ liệu một cách an toàn theo quyền hạn.
        </p>
        <div class="empty-features">
          <span class="flow-chip chip-read">
            <mat-icon class="sm" svgIcon="database" aria-hidden="true" />
            PostgreSQL SQL Guard
          </span>
          <span class="flow-chip chip-write">
            <mat-icon class="sm" svgIcon="key" aria-hidden="true" />
            Mã hóa OpenBao
          </span>
          <span class="flow-chip chip-warn">
            <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
            Phân quyền Cá nhân / Tổ chức
          </span>
        </div>
        @if (canCreate()) {
          <div class="empty-actions">
            <button
              mat-flat-button
              class="btn-aries"
              type="button"
              (click)="startCreate()"
            >
              <mat-icon svgIcon="add" /> Thêm kết nối đầu tiên
            </button>
          </div>
        }
      </div>
    }
  `,
  styles: `
    .panel,
    .conn-card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1.2rem 1.3rem;
    }
    .panel {
      margin-bottom: 1.25rem;
      border-color: var(--app-border-strong);
      box-shadow: var(--app-shadow-md);
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 1rem;
      padding-bottom: 0.85rem;
      border-bottom: 1px solid var(--app-border);
    }
    .panel-title {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      h2 {
        font-size: 1.05rem;
        margin: 0 0 0.1rem;
      }
      p {
        margin: 0;
      }
    }
    .conn-desc {
      margin: 0.15rem 0 0.85rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 0.3rem 0.9rem;
      align-items: center;
    }
    .grid .wide {
      grid-column: 1 / -1;
    }
    textarea.pem {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.78rem;
      line-height: 1.35;
    }
    textarea.secret {
      -webkit-text-security: disc; /* che khóa riêng khi nhập */
    }
    .oauth-box {
      padding: 0.85rem 1rem;
      border: 1px dashed var(--app-border-strong);
      background: var(--app-surface-2);
      border-radius: var(--app-radius-md);
      margin-bottom: 0.85rem;
      p {
        margin: 0 0 0.65rem;
      }
    }
    .panel-actions {
      display: flex;
      justify-content: flex-end;
      gap: 0.55rem;
      flex-wrap: wrap;
      margin-top: 1rem;
      padding-top: 0.85rem;
      border-top: 1px solid var(--app-border);
    }
    .conn-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 1.1rem;
    }
    .conn-card {
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 0.85rem;
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
      gap: 0.8rem;
    }
    .card-body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .title-line {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .cfg {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
    }
    .card-foot {
      display: flex;
      gap: 0.45rem;
      flex-wrap: wrap;
      padding-top: 0.7rem;
      border-top: 1px solid var(--app-border);
    }
    .danger {
      color: var(--app-danger, #dc2626) !important;
      border-color: var(--app-danger-border, #fecdd3) !important;
    }
  `,
})
export class Connections implements OnInit {
  private readonly api = inject(ConnectionsApi);
  private readonly oauthApi = inject(OAuthApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly providers = signal<OAuthProviderInfo[]>([]);
  protected readonly info = signal<string | null>(null);
  private readonly session = inject(SessionService);

  protected readonly connectors = signal<ConnectorInfo[]>([]);
  protected readonly connections = signal<ConnectionInfo[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly busyId = signal<string | null>(null);
  protected readonly editing = signal<{
    id: string | null;
    name: string;
    connectorId: string;
    scope: ConnectionScope;
    values: Record<string, unknown>;
    secrets: Record<string, string>;
  } | null>(null);

  protected readonly canCreate = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('builder', 'admin');
  });
  protected readonly isAdmin = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin');
  });
  protected readonly activeCount = computed(
    () => this.connections().filter((c) => c.status === 'active').length,
  );
  protected readonly workspaceCount = computed(
    () => this.connections().filter((c) => c.scope === 'workspace').length,
  );
  protected readonly connector = computed(() =>
    this.connectors().find((c) => c.id === this.editing()?.connectorId),
  );
  protected readonly fields = computed(() => {
    const c = this.connector();
    return c ? fieldsOf(c.config_schema) : [];
  });

  ngOnInit(): void {
    const q = this.route.snapshot.queryParamMap;
    if (q.get('oauth') === 'ok') this.info.set('Đã kết nối tài khoản thành công.');
    if (q.get('oauth_error')) this.error.set(q.get('oauth_error'));
    const wanted = q.get('connect');
    if (q.keys.length) {
      void this.router.navigate([], { queryParams: {}, replaceUrl: true });
    }
    this.oauthApi.providers().subscribe((p) => this.providers.set(p));
    this.api.connectors().subscribe((c) => {
      this.connectors.set(c);
      // /connect <provider>: mở sẵn form với connector dùng nhà cung cấp đó.
      const target = wanted ? c.find((x) => x.oauth_provider === wanted) : undefined;
      // Không chặn theo vai trò ở đây (hồ sơ có thể chưa tải xong); API vẫn kiểm quyền.
      if (target) {
        this.startCreate();
        this.pickConnector(target.id);
        const e = this.editing();
        if (e) this.editing.set({ ...e, connectorId: target.id, name: target.name });
      } else if (wanted) {
        this.error.set(`Không có connector dùng nhà cung cấp “${wanted}”.`);
      }
    });
    this.reload();
  }

  protected connectorName(id: string): string {
    return this.connectors().find((c) => c.id === id)?.name ?? id;
  }

  protected summary(c: ConnectionInfo): string[] {
    return Object.entries(c.config)
      .filter(([, v]) => v !== '' && v !== null && !(Array.isArray(v) && !v.length))
      .slice(0, 6)
      .map(([k, v]) => {
        const text = Array.isArray(v) ? v.join(',') : String(v);
        if (text.includes('-----BEGIN ')) return `${k}=(PEM)`;
        return `${k}=${text.length > 60 ? text.slice(0, 60) + '…' : text}`;
      });
  }

  /** Secret nhiều dòng (khóa riêng PEM) do connector khai báo trong ui_hints. */
  protected isMultilineSecret(c: ConnectorInfo, s: string): boolean {
    const list = c.ui_hints?.['multiline_secrets'];
    return Array.isArray(list) && list.includes(s);
  }

  protected providerName(id: string): string | null {
    return this.providers().find((p) => p.id === id)?.name ?? null;
  }

  protected startOAuth(): void {
    const e = this.editing();
    const c = this.connector();
    if (!e || !c?.oauth_provider) return;
    this.saving.set(true);
    this.error.set(null);
    this.oauthApi
      .start(c.oauth_provider, { connector_id: c.id, name: e.name.trim(), scope: e.scope })
      .subscribe({
        next: (r) => window.location.assign(r.authorize_url),
        error: (err) => this.fail(err),
      });
  }

  protected startCreate(): void {
    const first = this.connectors()[0];
    this.editing.set({
      id: null,
      name: '',
      connectorId: first?.id ?? '',
      scope: 'personal',
      values: first ? initialValues(first.config_schema) : {},
      secrets: {},
    });
  }

  protected startEdit(c: ConnectionInfo): void {
    const def = this.connectors().find((x) => x.id === c.connector_id);
    this.editing.set({
      id: c.id,
      name: c.name,
      connectorId: c.connector_id,
      scope: c.scope,
      values: def ? initialValues(def.config_schema, c.config) : {},
      secrets: {},
    });
  }

  protected pickConnector(id: string): void {
    const def = this.connectors().find((x) => x.id === id);
    const e = this.editing();
    if (e && def) this.editing.set({ ...e, values: initialValues(def.config_schema) });
  }

  protected save(): void {
    const e = this.editing();
    if (!e) return;
    this.saving.set(true);
    this.error.set(null);
    const config = toConfig(this.fields(), e.values);
    const secrets = Object.fromEntries(
      Object.entries(e.secrets).filter(([, v]) => !!v),
    );
    const request = e.id
      ? this.api.update(e.id, { name: e.name, config, secrets })
      : this.api.create({
          name: e.name,
          connector_id: e.connectorId,
          scope: e.scope,
          config,
          secrets,
        });
    request.subscribe({
      next: (saved) => {
        this.saving.set(false);
        this.editing.set(null);
        this.reload();
        this.test(saved);
      },
      error: (err) => this.fail(err),
    });
  }

  protected test(c: ConnectionInfo): void {
    this.busyId.set(c.id);
    this.api.test(c.id).subscribe({
      next: () => {
        this.busyId.set(null);
        this.reload();
      },
      error: (err) => {
        this.busyId.set(null);
        this.fail(err);
      },
    });
  }

  /** Connector có tool khám phá theo từng kết nối (OpenAPI, gRPC…) — ADR-0023. */
  protected isDynamic(c: ConnectionInfo): boolean {
    return !!this.connectors().find((x) => x.id === c.connector_id)?.dynamic_tools;
  }

  protected syncTools(c: ConnectionInfo): void {
    this.busyId.set(c.id);
    this.api.syncTools(c.id).subscribe({
      next: () => {
        this.busyId.set(null);
        this.reload();
      },
      error: (err) => {
        this.busyId.set(null);
        this.fail(err);
      },
    });
  }

  protected toggle(c: ConnectionInfo): void {
    this.api
      .update(c.id, { enabled: c.status !== 'active' })
      .subscribe({ next: () => this.reload(), error: (err) => this.fail(err) });
  }

  protected remove(c: ConnectionInfo): void {
    if (!confirm(`Xóa kết nối “${c.name}”? Secret sẽ bị xóa vĩnh viễn; agent đang dùng sẽ mất tool này.`)) {
      return;
    }
    this.api.remove(c.id).subscribe({ next: () => this.reload(), error: (err) => this.fail(err) });
  }

  private reload(): void {
    this.api.list().subscribe({
      next: (c) => this.connections.set(c),
      error: (err) => this.fail(err),
    });
  }

  private fail(err: { error?: { error?: { message?: string } } }): void {
    this.saving.set(false);
    this.error.set(err.error?.error?.message ?? 'Thao tác thất bại.');
  }
}

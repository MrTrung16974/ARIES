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
  kind: 'text' | 'number' | 'boolean' | 'select' | 'list';
  required: boolean;
  options: string[];
  hint: string;
}

/** Sinh trường form từ config_schema của connector (chỉ các kiểu đơn giản). */
export function fieldsOf(schema: JsonSchema): FieldDef[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([key, p]) => {
    let kind: FieldDef['kind'] = 'text';
    if (p.enum) kind = 'select';
    else if (p.type === 'integer' || p.type === 'number') kind = 'number';
    else if (p.type === 'boolean') kind = 'boolean';
    else if (p.type === 'array') kind = 'list';
    return {
      key,
      label: p.title ?? key,
      kind,
      required: required.has(key),
      options: (p.enum ?? []).map(String),
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
    <div class="section-head">
      <div>
        <h1>Kết nối dữ liệu</h1>
        <p class="muted subtitle">
          Kết nối tới hệ thống của bạn (CSDL…) để gắn vào agent. Mật khẩu được lưu
          trong kho bí mật OpenBao, không bao giờ hiển thị lại.
        </p>
      </div>
      @if (canCreate() && !editing()) {
        <button mat-flat-button class="btn-aries" type="button" (click)="startCreate()">
          <mat-icon svgIcon="add" /> Thêm kết nối
        </button>
      }
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }
    @if (info()) {
      <ui-notice severity="success">{{ info() }}</ui-notice>
    }

    @if (editing(); as e) {
      <form class="panel" (ngSubmit)="save()">
        <h2>{{ e.id ? 'Sửa kết nối' : 'Kết nối mới' }}</h2>
        <div class="grid">
          <mat-form-field appearance="outline">
            <mat-label>Tên kết nối</mat-label>
            <input matInput name="name" [(ngModel)]="e.name" required maxlength="80" />
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
              <mat-label>Phạm vi</mat-label>
              <mat-select name="scope" [(ngModel)]="e.scope">
                <mat-option value="personal">Cá nhân (chỉ mình tôi)</mat-option>
                @if (isAdmin()) {
                  <mat-option value="workspace">Dùng chung (cả tổ chức)</mat-option>
                }
              </mat-select>
            </mat-form-field>
          }
        </div>

        @if (connector(); as c) {
          <p class="muted small">{{ c.description }}</p>
          @if (c.oauth_provider && !e.id) {
            <div class="oauth-box">
              @if (providerName(c.oauth_provider); as pn) {
                <p class="small">
                  Kết nối bằng tài khoản {{ pn }} (OAuth). Bạn sẽ được chuyển sang trang đăng nhập
                  của {{ pn }}; ARIES chỉ lưu token trong kho bí mật, không thấy mật khẩu.
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
                  Chưa cấu hình nhà cung cấp OAuth “{{ c.oauth_provider }}” — liên hệ quản trị.
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
              <mat-form-field appearance="outline">
                <mat-label>
                  <mat-icon class="sm" svgIcon="key" aria-hidden="true" /> {{ s }}
                </mat-label>
                <input
                  matInput
                  type="password"
                  autocomplete="new-password"
                  [name]="'secret_' + s"
                  [(ngModel)]="e.secrets[s]"
                  [required]="!e.id"
                />
                @if (e.id) {
                  <mat-hint>Để trống = giữ nguyên giá trị đã lưu</mat-hint>
                }
              </mat-form-field>
            }
          </div>
        }

        <div class="actions">
          @if (!(connector()?.oauth_provider && !e.id)) {
            <button mat-flat-button class="btn-aries" type="submit" [disabled]="saving()">
              <mat-icon svgIcon="save" /> Lưu
            </button>
          }
          <button mat-stroked-button type="button" (click)="editing.set(null)">Hủy</button>
        </div>
      </form>
    }

    <div class="conn-grid">
      @for (c of connections(); track c.id) {
        <article class="conn-card">
          <div class="title-line">
            <mat-icon class="sm" svgIcon="database" aria-hidden="true" />
            <strong>{{ c.name }}</strong>
            <ui-tag [value]="connectorName(c.connector_id)" severity="primary" />
            <ui-tag
              [value]="c.scope === 'workspace' ? 'dùng chung' : 'cá nhân'"
              severity="secondary"
            />
            @if (c.status === 'disabled') {
              <ui-tag value="đang tắt" severity="warn" />
            }
          </div>
          <div class="muted small cfg">
            @for (kv of summary(c); track kv) {
              <span>{{ kv }}</span>
            }
          </div>
          @if (c.last_test_at) {
            <ui-notice [severity]="c.last_test_ok ? 'success' : 'error'" [compact]="true">
              {{ c.last_test_note }} · {{ c.last_test_at | date: 'short' }}
            </ui-notice>
          }
          @if (c.can_manage) {
            <div class="actions">
              <button mat-stroked-button type="button" (click)="test(c)" [disabled]="busyId() === c.id">
                <mat-icon svgIcon="play_arrow" /> Kiểm tra
              </button>
              <button mat-stroked-button type="button" (click)="startEdit(c)">
                <mat-icon svgIcon="edit" /> Sửa
              </button>
              <button mat-stroked-button type="button" (click)="toggle(c)">
                {{ c.status === 'active' ? 'Tắt' : 'Bật' }}
              </button>
              <button mat-stroked-button type="button" class="danger" (click)="remove(c)">
                <mat-icon svgIcon="delete" /> Xóa
              </button>
            </div>
          }
        </article>
      } @empty {
        <div class="empty-box muted">
          Chưa có kết nối nào.
          @if (canCreate()) {
            Bấm “Thêm kết nối” để tạo.
          }
        </div>
      }
    </div>
  `,
  styles: `
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 0.85rem;
      h1 { font-size: 1.25rem; margin: 0 0 0.15rem; }
      .subtitle { margin: 0; font-size: 0.88rem; }
    }
    .panel, .conn-card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1.1rem 1.2rem;
    }
    .panel { margin-bottom: 1.1rem; h2 { font-size: 1.05rem; margin: 0 0 0.8rem; } }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 0.2rem 0.9rem;
      align-items: center;
    }
    .oauth-box { padding: 0.7rem 0.9rem; border: 1px dashed var(--app-border); border-radius: var(--app-radius-md); margin-bottom: 0.6rem; }
    .actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.6rem; }
    .conn-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 1rem;
    }
    .conn-card { display: flex; flex-direction: column; gap: 0.55rem; }
    .title-line { display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .cfg { display: flex; flex-wrap: wrap; gap: 0.25rem 0.9rem; font-family: ui-monospace, monospace; }
    .danger { color: var(--app-danger, #dc2626); }
    .empty-box {
      padding: 2rem;
      text-align: center;
      border: 1px dashed var(--app-border);
      border-radius: var(--app-radius-lg);
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
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(',') : String(v)}`);
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

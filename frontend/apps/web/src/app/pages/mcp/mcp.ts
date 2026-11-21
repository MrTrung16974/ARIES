import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { McpApi, McpServerInfo, McpToolInfo, RiskLevel } from '@aiagent/core';
import { Notice, Severity, Tag } from '@aiagent/ui';

interface SecretRow {
  key: string;
  value: string;
}

const RISK_SEVERITY: Record<RiskLevel, Severity> = {
  read: 'info',
  write: 'warn',
  external_send: 'warn',
  destructive: 'danger',
};

const RISK_LABEL: Record<RiskLevel, string> = {
  read: 'Đọc',
  write: 'Ghi',
  external_send: 'Gửi ra ngoài',
  destructive: 'Phá hủy',
};

/** MCP server (ADR-0018) — Admin đăng ký; tool được khám phá tự động và đi qua policy/HITL. */
@Component({
  selector: 'app-mcp',
  imports: [
    DatePipe,
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
          <mat-icon class="sm" svgIcon="hub" />
        </span>
        <div class="hero-copy">
          <h1>MCP Server</h1>
          <p class="subtitle">
            Mở rộng công cụ cho AI Agent qua giao thức Model Context Protocol.
            Chỉ server do Admin đăng ký mới được kích hoạt; mọi tool đều đi qua
            kiểm duyệt rủi ro, phê duyệt thao tác (HITL) và nhật ký audit.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <button
          mat-stroked-button
          class="pill-btn"
          type="button"
          [disabled]="busy()"
          (click)="reload()"
        >
          <mat-icon svgIcon="refresh" /> Tải lại
        </button>
        @if (!creating()) {
          <button
            mat-flat-button
            class="btn-aries"
            type="button"
            (click)="startCreate()"
          >
            <mat-icon svgIcon="add" /> Đăng ký server
          </button>
        }
      </div>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon" aria-hidden="true">
          <mat-icon class="sm" svgIcon="hub" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ servers().length }}</span>
          <span class="kpi-label">MCP server đã đăng ký</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="check_circle" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ enabledCount() }}</span>
          <span class="kpi-label">Đang hoạt động</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="bolt" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ totalToolsCount() }}</span>
          <span class="kpi-label">Tool đã đồng bộ</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon warn" aria-hidden="true">
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">Allowlist</span>
          <span class="kpi-label">Egress proxy &amp; HITL</span>
        </div>
      </div>
    </div>

    <div class="flow-info-strip">
      <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
      <span>
        Server HTTP kết nối ra Internet phải có tên miền nằm trong danh sách cho
        phép (allowlist) của egress proxy. Secret được mã hóa trong OpenBao.
      </span>
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }

    @if (creating()) {
      <form class="panel" (ngSubmit)="create()">
        <div class="panel-head">
          <div class="panel-title">
            <span class="icon-badge" aria-hidden="true">
              <mat-icon class="sm" svgIcon="add" />
            </span>
            <div>
              <h2>Đăng ký MCP server mới</h2>
              <p class="muted small">
                Hệ thống sẽ tự động kết nối và khám phá danh sách tool sau khi
                lưu.
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

        <div class="grid">
          <mat-form-field appearance="outline">
            <mat-label>Tên hiển thị</mat-label>
            <input
              matInput
              name="name"
              [(ngModel)]="form.name"
              placeholder="Ví dụ: Jira nội bộ"
              required
              maxlength="80"
            />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Mã định danh (slug)</mat-label>
            <input
              matInput
              name="slug"
              [(ngModel)]="form.slug"
              placeholder="jira_internal"
              required
              pattern="[a-z][a-z0-9_]{1,30}"
            />
            <mat-hint>Chữ thường, số và dấu gạch dưới (_)</mat-hint>
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Kiểu kết nối</mat-label>
            <mat-select name="transport" [(ngModel)]="form.transport">
              <mat-option value="http">Streamable HTTP (URL)</mat-option>
              <mat-option value="stdio"
                >Lệnh cục bộ (stdio, đã cài sẵn)</mat-option
              >
            </mat-select>
          </mat-form-field>
          @if (form.transport === 'http') {
            <mat-form-field appearance="outline">
              <mat-label>Endpoint URL</mat-label>
              <input
                matInput
                name="url"
                [(ngModel)]="form.url"
                placeholder="https://mcp.example.com/mcp"
              />
            </mat-form-field>
          } @else {
            <mat-form-field appearance="outline">
              <mat-label>Lệnh đã duyệt</mat-label>
              <mat-select name="command" [(ngModel)]="form.command_key">
                @for (c of commands(); track c) {
                  <mat-option [value]="c">{{ c }}</mat-option>
                }
              </mat-select>
              <mat-hint
                >Cấu hình qua biến AIAGENT_MCP_STDIO_COMMANDS</mat-hint
              >
            </mat-form-field>
          }
        </div>

        <div class="secrets-section">
          <div class="secrets-head">
            <h3 class="sec-title">
              <mat-icon class="sm" svgIcon="key" aria-hidden="true" />
              <span>
                Secret
                {{
                  form.transport === 'http'
                    ? '(Header HTTP)'
                    : '(Biến môi trường)'
                }}
              </span>
            </h3>
            <button
              mat-stroked-button
              class="pill-btn"
              type="button"
              (click)="secrets.push({ key: '', value: '' })"
            >
              <mat-icon svgIcon="add" /> Thêm secret
            </button>
          </div>
          @for (row of secrets; track $index) {
            <div class="secret-row">
              <mat-form-field appearance="outline" subscriptSizing="dynamic">
                <mat-label>Tên khóa</mat-label>
                <input
                  matInput
                  [name]="'k' + $index"
                  [(ngModel)]="row.key"
                  [placeholder]="
                    form.transport === 'http' ? 'X-Api-Key' : 'API_TOKEN'
                  "
                />
              </mat-form-field>
              <mat-form-field appearance="outline" subscriptSizing="dynamic">
                <mat-label>Giá trị bí mật</mat-label>
                <input
                  matInput
                  type="password"
                  autocomplete="new-password"
                  [name]="'v' + $index"
                  [(ngModel)]="row.value"
                />
              </mat-form-field>
              <button
                mat-icon-button
                type="button"
                class="remove-secret"
                aria-label="Xóa dòng secret"
                [disabled]="secrets.length === 1"
                (click)="secrets.splice($index, 1)"
              >
                <mat-icon class="sm" svgIcon="delete" />
              </button>
            </div>
          }
        </div>

        <div class="panel-actions">
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
            [disabled]="busy()"
          >
            <mat-icon svgIcon="save" />
            {{ busy() ? 'Đang lưu & đồng bộ…' : 'Lưu và lấy danh sách tool' }}
          </button>
        </div>
      </form>
    }

    <div class="list">
      @for (s of servers(); track s.id) {
        <article class="card">
          <div class="card-head">
            <div class="title-line">
              <span class="icon-badge" aria-hidden="true">
                <mat-icon class="sm" svgIcon="hub" />
              </span>
              <div class="server-meta">
                <div class="server-name-row">
                  <strong>{{ s.name }}</strong>
                  <code>{{ s.slug }}</code>
                  <ui-tag [value]="s.transport.toUpperCase()" severity="secondary" />
                  <ui-tag
                    [value]="s.enabled ? 'Đang bật' : 'Đang tắt'"
                    [severity]="s.enabled ? 'success' : 'warn'"
                  />
                </div>
                <span class="muted small endpoint">{{
                  s.url || s.command_key
                }}</span>
              </div>
            </div>

            <div class="card-actions">
              <button
                mat-stroked-button
                class="pill-btn"
                type="button"
                (click)="sync(s)"
                [disabled]="busy()"
              >
                <mat-icon svgIcon="sync" /> Đồng bộ tool
              </button>
              <button
                mat-stroked-button
                class="pill-btn"
                type="button"
                (click)="toggle(s)"
              >
                {{ s.enabled ? 'Tắt' : 'Bật' }}
              </button>
              <button
                mat-stroked-button
                class="pill-btn danger"
                type="button"
                (click)="remove(s)"
              >
                <mat-icon svgIcon="delete" /> Xóa
              </button>
            </div>
          </div>

          @if (s.last_error) {
            <ui-notice severity="error" [compact]="true">{{
              s.last_error
            }}</ui-notice>
          } @else if (s.last_sync_at) {
            <div class="sync-meta">
              <span class="flow-chip chip-read">
                <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
                {{ s.tools.length }} tool
              </span>
              <span class="flow-chip chip-warn">
                <mat-icon class="sm" svgIcon="refresh" aria-hidden="true" />
                Đồng bộ {{ s.last_sync_at | date: 'dd/MM HH:mm' }}
              </span>
              @if (s.secret_keys.length) {
                <span class="flow-chip chip-write">
                  <mat-icon class="sm" svgIcon="key" aria-hidden="true" />
                  Secret: {{ s.secret_keys.join(', ') }}
                </span>
              }
            </div>
          }

          @if (s.tools.length) {
            <div class="tools-wrap">
              <table class="tools">
                <thead>
                  <tr>
                    <th>Tool trong agent</th>
                    <th>Mô tả</th>
                    <th>Mức độ rủi ro</th>
                  </tr>
                </thead>
                <tbody>
                  @for (t of s.tools; track t.name) {
                    <tr>
                      <td>
                        <code>{{ t.tool }}</code>
                      </td>
                      <td class="muted small">{{ t.description || '—' }}</td>
                      <td>
                        <div class="risk-cell">
                          <mat-select
                            class="risk"
                            [value]="t.risk"
                            (valueChange)="setRisk(s, t, $event)"
                            [attr.aria-label]="'Mức rủi ro của ' + t.tool"
                          >
                            @for (r of risks; track r) {
                              <mat-option [value]="r">{{
                                riskLabel[r]
                              }}</mat-option>
                            }
                          </mat-select>
                          <ui-tag
                            [value]="riskLabel[t.risk]"
                            [severity]="riskSeverity[t.risk]"
                          />
                          @if (t.risk !== t.detected_risk) {
                            <span class="muted small"
                              >(tự nhận: {{ riskLabel[t.detected_risk] }})</span
                            >
                          }
                        </div>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </article>
      } @empty {
        @if (!creating()) {
          <div class="empty-state-card">
            <div class="empty-icon-ring" aria-hidden="true">
              <mat-icon svgIcon="hub" />
            </div>
            <h2>Chưa có MCP server nào được đăng ký</h2>
            <p>
              Kết nối các MCP server nội bộ hoặc đối tác (Streamable HTTP hoặc
              stdio) để tự động nạp công cụ mới cho AI Agent mà không cần viết
              lại mã nguồn.
            </p>
            <div class="empty-features">
              <span class="flow-chip chip-read">
                <mat-icon class="sm" svgIcon="sync" aria-hidden="true" />
                Tự động khám phá tool
              </span>
              <span class="flow-chip chip-write">
                <mat-icon class="sm" svgIcon="gavel" aria-hidden="true" />
                Phân loại rủi ro &amp; duyệt HITL
              </span>
              <span class="flow-chip chip-warn">
                <mat-icon class="sm" svgIcon="key" aria-hidden="true" />
                Lưu secret an toàn trên OpenBao
              </span>
            </div>
            <div class="empty-actions">
              <button
                mat-flat-button
                class="btn-aries"
                type="button"
                (click)="startCreate()"
              >
                <mat-icon svgIcon="add" /> Đăng ký MCP server đầu tiên
              </button>
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: `
    .panel,
    .card {
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-sm);
      padding: 1.25rem 1.35rem;
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
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 0.35rem 0.9rem;
    }
    .secrets-section {
      margin-top: 0.5rem;
      padding: 0.9rem 1rem;
      border-radius: var(--app-radius-lg);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }
    .secrets-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
      h3 {
        margin: 0;
        font-size: 0.88rem;
      }
    }
    .secret-row {
      display: grid;
      grid-template-columns: 1fr 1fr auto;
      gap: 0.75rem;
      align-items: center;
    }
    .remove-secret {
      color: var(--app-muted);
      &:hover:not(:disabled) {
        color: var(--app-danger);
      }
    }
    .panel-actions {
      display: flex;
      justify-content: flex-end;
      gap: 0.55rem;
      flex-wrap: wrap;
      margin-top: 1.1rem;
      padding-top: 0.9rem;
      border-top: 1px solid var(--app-border);
    }
    .list {
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .card {
      display: flex;
      flex-direction: column;
      gap: 0.8rem;
      transition:
        border-color 0.16s ease,
        box-shadow 0.16s ease;
      &:hover {
        border-color: var(--app-border-strong);
        box-shadow: var(--app-shadow-md);
      }
    }
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .title-line {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      min-width: 0;
      flex: 1;
    }
    .server-meta {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      min-width: 0;
    }
    .server-name-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .endpoint {
      font-family: ui-monospace, monospace;
      word-break: break-all;
    }
    .card-actions {
      display: flex;
      gap: 0.45rem;
      flex-wrap: wrap;
      flex-shrink: 0;
    }
    .sync-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
    }
    .tools-wrap {
      overflow-x: auto;
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-md);
    }
    table.tools {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
      thead tr {
        background: var(--app-surface-2);
      }
      th {
        font-size: 0.74rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--app-muted);
        font-weight: 600;
      }
      th,
      td {
        text-align: left;
        padding: 0.55rem 0.75rem;
        border-bottom: 1px solid var(--app-border);
        vertical-align: middle;
      }
      tbody tr:last-child td {
        border-bottom: none;
      }
    }
    .risk-cell {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .risk {
      width: 8.5rem;
      padding: 0.2rem 0.45rem;
      border-radius: 8px;
      border: 1px solid var(--app-border);
      background: var(--app-surface-2);
    }
    .danger {
      color: var(--app-danger, #dc2626) !important;
      border-color: var(--app-danger-border, #fecdd3) !important;
    }
    @media (max-width: 640px) {
      .secret-row {
        grid-template-columns: 1fr;
      }
    }
  `,
})
export class Mcp implements OnInit {
  private readonly api = inject(McpApi);

  protected readonly servers = signal<McpServerInfo[]>([]);
  protected readonly commands = signal<string[]>([]);
  protected readonly creating = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly risks: RiskLevel[] = [
    'read',
    'write',
    'external_send',
    'destructive',
  ];
  protected readonly riskSeverity = RISK_SEVERITY;
  protected readonly riskLabel = RISK_LABEL;
  protected form = this.emptyForm();
  protected secrets: SecretRow[] = [];

  protected readonly enabledCount = computed(
    () => this.servers().filter((s) => s.enabled).length,
  );
  protected readonly totalToolsCount = computed(() =>
    this.servers().reduce((acc, s) => acc + (s.tools?.length ?? 0), 0),
  );

  ngOnInit(): void {
    this.reload();
    this.api.commands().subscribe((c) => this.commands.set(c));
  }

  protected startCreate(): void {
    this.form = this.emptyForm();
    this.secrets = [{ key: '', value: '' }];
    this.creating.set(true);
  }

  protected create(): void {
    this.busy.set(true);
    this.error.set(null);
    const secrets = Object.fromEntries(
      this.secrets
        .filter((r) => r.key.trim() && r.value)
        .map((r) => [r.key.trim(), r.value]),
    );
    this.api
      .create({
        ...this.form,
        url: this.form.transport === 'http' ? this.form.url : null,
        command_key:
          this.form.transport === 'stdio' ? this.form.command_key : null,
        secrets,
      })
      .subscribe({
        next: () => {
          this.busy.set(false);
          this.creating.set(false);
          this.secrets = [];
          this.reload();
        },
        error: (err) => this.fail(err),
      });
  }

  protected sync(s: McpServerInfo): void {
    this.busy.set(true);
    this.api.sync(s.id).subscribe({
      next: () => {
        this.busy.set(false);
        this.reload();
      },
      error: (err) => this.fail(err),
    });
  }

  protected toggle(s: McpServerInfo): void {
    this.api.update(s.id, { enabled: !s.enabled }).subscribe({
      next: () => this.reload(),
      error: (err) => this.fail(err),
    });
  }

  protected setRisk(s: McpServerInfo, t: McpToolInfo, risk: RiskLevel): void {
    const overrides: Record<string, string> = {};
    for (const tool of s.tools) {
      const value = tool.name === t.name ? risk : tool.risk;
      if (value !== tool.detected_risk) overrides[tool.name] = value;
    }
    this.api.update(s.id, { risk_overrides: overrides }).subscribe({
      next: () => this.reload(),
      error: (err) => this.fail(err),
    });
  }

  protected remove(s: McpServerInfo): void {
    if (
      !confirm(
        `Xóa MCP server “${s.name}”? Agent đang dùng sẽ mất các tool này.`,
      )
    )
      return;
    this.api.remove(s.id).subscribe({
      next: () => this.reload(),
      error: (err) => this.fail(err),
    });
  }

  protected reload(): void {
    this.api.list().subscribe({
      next: (list) => this.servers.set(list),
      error: (err) => this.fail(err),
    });
  }

  private emptyForm(): {
    name: string;
    slug: string;
    transport: 'http' | 'stdio';
    url: string;
    command_key: string;
  } {
    return { name: '', slug: '', transport: 'http', url: '', command_key: '' };
  }

  private fail(err: { error?: { error?: { message?: string } } }): void {
    this.busy.set(false);
    this.error.set(err.error?.error?.message ?? 'Thao tác thất bại.');
  }
}

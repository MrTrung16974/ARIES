import { DatePipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
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
    <div class="section-head">
      <div>
        <h1>MCP server</h1>
        <p class="muted subtitle">
          Mở rộng tool cho agent qua Model Context Protocol. Chỉ server do Admin đăng ký mới
          dùng được; mọi tool vẫn qua kiểm duyệt rủi ro, duyệt thao tác và audit. Server HTTP ra
          Internet phải có domain trong allowlist của egress proxy.
        </p>
      </div>
      @if (!creating()) {
        <button mat-flat-button class="btn-aries" type="button" (click)="startCreate()">
          <mat-icon svgIcon="add" /> Đăng ký server
        </button>
      }
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }

    @if (creating()) {
      <form class="panel" (ngSubmit)="create()">
        <h2>Đăng ký MCP server</h2>
        <div class="grid">
          <mat-form-field appearance="outline">
            <mat-label>Tên hiển thị</mat-label>
            <input matInput name="name" [(ngModel)]="form.name" required maxlength="80" />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Mã (chữ thường, số, _)</mat-label>
            <input matInput name="slug" [(ngModel)]="form.slug" required pattern="[a-z][a-z0-9_]{1,30}" />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Kiểu kết nối</mat-label>
            <mat-select name="transport" [(ngModel)]="form.transport">
              <mat-option value="http">Streamable HTTP (URL)</mat-option>
              <mat-option value="stdio">Lệnh cục bộ (stdio, đã được cài)</mat-option>
            </mat-select>
          </mat-form-field>
          @if (form.transport === 'http') {
            <mat-form-field appearance="outline">
              <mat-label>URL</mat-label>
              <input matInput name="url" [(ngModel)]="form.url" placeholder="https://…/mcp" />
            </mat-form-field>
          } @else {
            <mat-form-field appearance="outline">
              <mat-label>Lệnh</mat-label>
              <mat-select name="command" [(ngModel)]="form.command_key">
                @for (c of commands(); track c) {
                  <mat-option [value]="c">{{ c }}</mat-option>
                }
              </mat-select>
              <mat-hint>Danh sách do người vận hành cho phép (AIAGENT_MCP_STDIO_COMMANDS)</mat-hint>
            </mat-form-field>
          }
        </div>
        <h3 class="small">
          Secret {{ form.transport === 'http' ? '(header HTTP)' : '(biến môi trường)' }}
        </h3>
        @for (row of secrets; track $index) {
          <div class="secret-row">
            <mat-form-field appearance="outline">
              <mat-label>Tên</mat-label>
              <input matInput [name]="'k' + $index" [(ngModel)]="row.key"
                     [placeholder]="form.transport === 'http' ? 'X-Api-Key' : 'API_TOKEN'" />
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Giá trị</mat-label>
              <input matInput type="password" autocomplete="new-password"
                     [name]="'v' + $index" [(ngModel)]="row.value" />
            </mat-form-field>
          </div>
        }
        <button mat-stroked-button type="button" (click)="secrets.push({ key: '', value: '' })">
          <mat-icon svgIcon="add" /> Thêm secret
        </button>
        <div class="actions">
          <button mat-flat-button class="btn-aries" type="submit" [disabled]="busy()">
            <mat-icon svgIcon="save" /> Lưu và lấy danh sách tool
          </button>
          <button mat-stroked-button type="button" (click)="creating.set(false)">Hủy</button>
        </div>
      </form>
    }

    <div class="list">
      @for (s of servers(); track s.id) {
        <article class="card">
          <div class="title-line">
            <mat-icon class="sm" svgIcon="hub" aria-hidden="true" />
            <strong>{{ s.name }}</strong>
            <code>{{ s.slug }}</code>
            <ui-tag [value]="s.transport" severity="secondary" />
            @if (!s.enabled) {
              <ui-tag value="đang tắt" severity="warn" />
            }
            <span class="spacer"></span>
            <span class="muted small">{{ s.url || s.command_key }}</span>
          </div>
          @if (s.last_error) {
            <ui-notice severity="error" [compact]="true">{{ s.last_error }}</ui-notice>
          } @else if (s.last_sync_at) {
            <p class="muted small">
              {{ s.tools.length }} tool · đồng bộ {{ s.last_sync_at | date: 'short' }}
              @if (s.secret_keys.length) {
                · secret: {{ s.secret_keys.join(', ') }}
              }
            </p>
          }
          @if (s.tools.length) {
            <table class="tools">
              <thead>
                <tr><th>Tool trong agent</th><th>Mô tả</th><th>Rủi ro</th></tr>
              </thead>
              <tbody>
                @for (t of s.tools; track t.name) {
                  <tr>
                    <td><code>{{ t.tool }}</code></td>
                    <td class="muted small">{{ t.description }}</td>
                    <td>
                      <mat-select
                        class="risk"
                        [value]="t.risk"
                        (valueChange)="setRisk(s, t, $event)"
                        [attr.aria-label]="'Mức rủi ro của ' + t.tool"
                      >
                        @for (r of risks; track r) {
                          <mat-option [value]="r">{{ r }}</mat-option>
                        }
                      </mat-select>
                      <ui-tag [value]="t.risk" [severity]="riskSeverity[t.risk]" />
                      @if (t.risk !== t.detected_risk) {
                        <span class="muted small">(tự nhận: {{ t.detected_risk }})</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
          <div class="actions">
            <button mat-stroked-button type="button" (click)="sync(s)" [disabled]="busy()">
              <mat-icon svgIcon="sync" /> Đồng bộ tool
            </button>
            <button mat-stroked-button type="button" (click)="toggle(s)">
              {{ s.enabled ? 'Tắt' : 'Bật' }}
            </button>
            <button mat-stroked-button type="button" class="danger" (click)="remove(s)">
              <mat-icon svgIcon="delete" /> Xóa
            </button>
          </div>
        </article>
      } @empty {
        <div class="empty-box muted">Chưa có MCP server nào.</div>
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
    .panel, .card {
      background: var(--app-surface); border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl); box-shadow: var(--app-shadow-sm); padding: 1.1rem 1.2rem;
    }
    .panel { margin-bottom: 1.1rem; h2 { font-size: 1.05rem; margin: 0 0 0.8rem; } }
    .grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 0.2rem 0.9rem;
    }
    .secret-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; }
    .actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.6rem; }
    .list { display: flex; flex-direction: column; gap: 1rem; }
    .title-line { display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .spacer { flex: 1; }
    table.tools { width: 100%; border-collapse: collapse; margin-top: 0.4rem; font-size: 0.86rem; }
    table.tools th, table.tools td {
      text-align: left; padding: 0.35rem 0.4rem; border-bottom: 1px solid var(--app-border);
      vertical-align: middle;
    }
    .risk { width: 9rem; display: inline-block; margin-right: 0.4rem; }
    .danger { color: var(--app-danger, #dc2626); }
    .empty-box {
      padding: 2rem; text-align: center; border: 1px dashed var(--app-border);
      border-radius: var(--app-radius-lg);
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
  protected readonly risks: RiskLevel[] = ['read', 'write', 'external_send', 'destructive'];
  protected readonly riskSeverity = RISK_SEVERITY;
  protected form = this.emptyForm();
  protected secrets: SecretRow[] = [];

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
      this.secrets.filter((r) => r.key.trim() && r.value).map((r) => [r.key.trim(), r.value]),
    );
    this.api
      .create({
        ...this.form,
        url: this.form.transport === 'http' ? this.form.url : null,
        command_key: this.form.transport === 'stdio' ? this.form.command_key : null,
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
    if (!confirm(`Xóa MCP server “${s.name}”? Agent đang dùng sẽ mất các tool này.`)) return;
    this.api.remove(s.id).subscribe({ next: () => this.reload(), error: (err) => this.fail(err) });
  }

  private reload(): void {
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

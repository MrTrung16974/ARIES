import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import {
  AgentDetail,
  AgentsApi,
  ApiKeyInfo,
  APP_CONFIG,
  ChannelsApi,
  ShareLinkInfo,
  TelegramBotInfo,
} from '@aiagent/core';
import { Notice, Tag } from '@aiagent/ui';

/** Publish agent ra ngoài (ADR-0020): link chia sẻ / widget nhúng, API key, Telegram. */
@Component({
  selector: 'app-agent-publish',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatSelectModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="pub-top">
      <a [routerLink]="['/agents', id(), 'edit']" class="back-link">
        <mat-icon class="sm" svgIcon="arrow_back" aria-hidden="true" />
        <span>Quay lại cấu hình Agent</span>
      </a>
    </div>

    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="share" />
        </span>
        <div class="hero-copy">
          <h1>
            <span>Chia sẻ &amp; API</span>
            @if (agent(); as a) {
              <ui-tag [value]="a.name" severity="primary" />
              <ui-tag
                [value]="
                  a.published_version
                    ? 'Đang chạy v' + a.published_version
                    : 'Chưa publish'
                "
                [severity]="a.published_version ? 'success' : 'warn'"
              />
            }
          </h1>
          <p class="subtitle">
            Phát hành Agent qua link công khai, widget nhúng website, REST API
            key hoặc bot Telegram.
          </p>
        </div>
      </div>
      <div class="hero-actions">
        <a
          mat-stroked-button
          class="pill-btn"
          [routerLink]="['/agents', id(), 'chat']"
        >
          <mat-icon svgIcon="forum" /> Mở chat
        </a>
        <a
          mat-stroked-button
          class="pill-btn"
          [routerLink]="['/agents', id(), 'edit']"
        >
          <mat-icon svgIcon="edit" /> Sửa Agent
        </a>
      </div>
    </div>

    <div class="flow-info-strip">
      <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
      <span>
        Kênh ngoài luôn chạy phiên bản đã publish, tự động chặn thao tác cần
        duyệt (HITL) và không truy cập tài nguyên cá nhân. Link công khai không
        sử dụng được kết nối dữ liệu hoặc MCP.
      </span>
    </div>

    <div class="kpi-strip">
      <div class="kpi-card">
        <span class="kpi-icon" aria-hidden="true">
          <mat-icon class="sm" svgIcon="public" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ activeSharesCount() }}/{{ shares().length }}</span>
          <span class="kpi-label">Link chia sẻ đang bật</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon info" aria-hidden="true">
          <mat-icon class="sm" svgIcon="key" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ activeKeysCount() }}</span>
          <span class="kpi-label">API key đang hoạt động</span>
        </div>
      </div>
      <div class="kpi-card">
        <span class="kpi-icon ok" aria-hidden="true">
          <mat-icon class="sm" svgIcon="forum" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ activeBotsCount() }}</span>
          <span class="kpi-label">Bot Telegram đang bật</span>
        </div>
      </div>
      <div class="kpi-card">
        <span
          class="kpi-icon"
          [class.ok]="isPublic()"
          [class.warn]="!isPublic()"
          aria-hidden="true"
        >
          <mat-icon class="sm" svgIcon="shield" />
        </span>
        <div class="kpi-meta">
          <span class="kpi-val">{{ isPublic() ? 'Công khai' : 'Nội bộ / Mật' }}</span>
          <span class="kpi-label">Phân loại dữ liệu</span>
        </div>
      </div>
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }
    @if (copied()) {
      <ui-notice severity="success">{{ copied() }}</ui-notice>
    }

    @if (secret(); as s) {
      <section class="panel-card secret-card">
        <div class="secret-head">
          <ui-notice severity="warn" [compact]="true">
            {{ s.label }} chỉ hiển thị <strong>một lần duy nhất</strong> — hãy
            sao chép và lưu giữ ngay.
          </ui-notice>
          <button
            mat-stroked-button
            class="pill-btn"
            type="button"
            (click)="secret.set(null)"
          >
            <mat-icon svgIcon="check" /> Đã lưu, ẩn đi
          </button>
        </div>

        <div class="copy-row">
          <code>{{ s.value }}</code>
          <button
            mat-flat-button
            class="btn-aries"
            type="button"
            (click)="copy(s.value, s.label)"
          >
            <mat-icon svgIcon="content_copy" /> Sao chép
          </button>
        </div>

        @if (s.embed) {
          <p class="small muted snippet-label">
            Mã nhúng website (dán trước thẻ đóng &lt;/body&gt;):
          </p>
          <div class="copy-row">
            <code>{{ s.embed }}</code>
            <button
              mat-stroked-button
              class="pill-btn"
              type="button"
              (click)="copy(s.embed, 'Mã nhúng widget')"
            >
              <mat-icon svgIcon="content_copy" /> Sao chép
            </button>
          </div>
        }
        @if (s.curl) {
          <p class="small muted snippet-label">Ví dụ gọi thử bằng cURL:</p>
          <pre class="code-box">{{ s.curl }}</pre>
        }
      </section>
    }

    <!-- 1. Share links & widget -->
    <section class="panel-card">
      <div class="section-head">
        <div class="section-head-left">
          <span class="sec-badge" aria-hidden="true">
            <mat-icon class="sm" svgIcon="public" />
          </span>
          <div>
            <h2>Link chia sẻ &amp; Widget nhúng</h2>
            <p class="muted small">
              Tạo trang chat công khai hoặc nhúng bong bóng chat trực tiếp lên
              website của tổ chức.
            </p>
          </div>
        </div>
        <ui-tag [value]="shares().length + ' link'" severity="secondary" />
      </div>

      @if (!isPublic()) {
        <ui-notice severity="info" [compact]="true">
          Chỉ agent có phân loại dữ liệu “Công khai (public)” mới tạo được link
          công khai. Đổi ở tab Tổng quan của trang Sửa Agent rồi publish lại.
        </ui-notice>
      } @else {
        <form class="inline-form" (ngSubmit)="createShare()">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <mat-label>Tên link</mat-label>
            <input matInput name="sname" [(ngModel)]="shareForm.name" required />
          </mat-form-field>
          <mat-form-field
            appearance="outline"
            subscriptSizing="dynamic"
            class="grow"
          >
            <mat-label>Chỉ cho nhúng ở (trống = mọi nơi)</mat-label>
            <input
              matInput
              name="origins"
              [(ngModel)]="shareForm.origins"
              placeholder="https://www.congty.vn, https://intranet.congty.vn"
            />
          </mat-form-field>
          <mat-form-field
            appearance="outline"
            subscriptSizing="dynamic"
            class="num-field"
          >
            <mat-label>Tin/phút</mat-label>
            <input
              matInput
              type="number"
              name="rate"
              [(ngModel)]="shareForm.rate"
            />
          </mat-form-field>
          <mat-form-field
            appearance="outline"
            subscriptSizing="dynamic"
            class="num-field"
          >
            <mat-label>Tin/ngày</mat-label>
            <input
              matInput
              type="number"
              name="daily"
              [(ngModel)]="shareForm.daily"
            />
          </mat-form-field>
          <button mat-flat-button class="btn-aries" type="submit">
            <mat-icon svgIcon="add" /> Tạo link
          </button>
        </form>
      }

      @if (shares().length === 0) {
        <p class="muted small empty-row">Chưa có link chia sẻ nào.</p>
      } @else {
        <div class="table-wrap">
          <table class="aries-table">
            <thead>
              <tr>
                <th>Tên &amp; Giới hạn</th>
                <th>Trạng thái</th>
                <th>Hoạt động</th>
                <th class="act"></th>
              </tr>
            </thead>
            <tbody>
              @for (s of shares(); track s.id) {
                <tr [class.revoked]="!s.enabled">
                  <td>
                    <strong>{{ s.name }}</strong>
                    <div class="muted small">
                      <code>{{ s.prefix }}…</code> ·
                      {{ s.rate_limit_per_min }}/phút ·
                      {{ s.daily_message_limit }}/ngày
                      @if (s.allowed_origins.length) {
                        · nhúng ở: {{ s.allowed_origins.join(', ') }}
                      }
                    </div>
                  </td>
                  <td>
                    <ui-tag
                      [value]="s.enabled ? 'Đang bật' : 'Đã tắt'"
                      [severity]="s.enabled ? 'success' : 'secondary'"
                    />
                  </td>
                  <td class="muted small">
                    {{
                      s.last_used_at
                        ? 'Dùng ' + (s.last_used_at | date: 'dd/MM HH:mm')
                        : 'Chưa dùng'
                    }}
                  </td>
                  <td class="act">
                    <button
                      mat-stroked-button
                      class="pill-btn"
                      type="button"
                      (click)="toggleShare(s)"
                    >
                      {{ s.enabled ? 'Tắt' : 'Bật' }}
                    </button>
                    <button
                      mat-stroked-button
                      class="pill-btn"
                      type="button"
                      (click)="rotateShare(s)"
                    >
                      Tạo lại link
                    </button>
                    <button
                      mat-icon-button
                      type="button"
                      class="del-icon-btn"
                      aria-label="Xóa link"
                      (click)="deleteShare(s)"
                    >
                      <mat-icon svgIcon="delete" />
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </section>

    <!-- 2. API keys -->
    <section class="panel-card">
      <div class="section-head">
        <div class="section-head-left">
          <span class="sec-badge" aria-hidden="true">
            <mat-icon class="sm" svgIcon="key" />
          </span>
          <div>
            <h2>API Key</h2>
            <p class="muted small">
              Gọi agent từ hệ thống khác qua
              <code>POST {{ apiBase }}/v1/public/v1/chat</code> với header
              <code>Authorization: Bearer &lt;key&gt;</code>. Truyền
              <code>user</code> để tách hội thoại theo người dùng cuối.
            </p>
          </div>
        </div>
        <ui-tag [value]="keys().length + ' key'" severity="secondary" />
      </div>

      <form class="inline-form" (ngSubmit)="createKey()">
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="grow"
        >
          <mat-label>Tên key (hệ thống sử dụng)</mat-label>
          <input
            matInput
            name="kname"
            [(ngModel)]="keyForm.name"
            required
            placeholder="Ví dụ: CRM Nội bộ"
          />
        </mat-form-field>
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="num-field"
        >
          <mat-label>Yêu cầu/phút</mat-label>
          <input
            matInput
            type="number"
            name="krate"
            [(ngModel)]="keyForm.rate"
          />
        </mat-form-field>
        <button mat-flat-button class="btn-aries" type="submit">
          <mat-icon svgIcon="add" /> Tạo key
        </button>
      </form>

      @if (keys().length === 0) {
        <p class="muted small empty-row">Chưa có API key nào.</p>
      } @else {
        <div class="table-wrap">
          <table class="aries-table">
            <thead>
              <tr>
                <th>Tên key</th>
                <th>Trạng thái</th>
                <th class="act"></th>
              </tr>
            </thead>
            <tbody>
              @for (k of keys(); track k.id) {
                <tr [class.revoked]="k.revoked_at">
                  <td>
                    <strong>{{ k.name }}</strong>
                    <div class="muted small">
                      <code>{{ k.prefix }}…</code> ·
                      {{ k.rate_limit_per_min }} yêu cầu/phút
                    </div>
                  </td>
                  <td>
                    @if (k.revoked_at) {
                      <ui-tag value="Đã thu hồi" severity="danger" />
                    } @else {
                      <span class="muted small">
                        {{
                          k.last_used_at
                            ? 'Dùng ' + (k.last_used_at | date: 'dd/MM HH:mm')
                            : 'Chưa dùng'
                        }}
                      </span>
                    }
                  </td>
                  <td class="act">
                    @if (!k.revoked_at) {
                      <button
                        mat-stroked-button
                        class="pill-btn danger-btn"
                        type="button"
                        (click)="revoke(k)"
                      >
                        Thu hồi
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

    <!-- 3. Telegram -->
    <section class="panel-card">
      <div class="section-head">
        <div class="section-head-left">
          <span class="sec-badge" aria-hidden="true">
            <mat-icon class="sm" svgIcon="forum" />
          </span>
          <div>
            <h2>Bot Telegram</h2>
            <p class="muted small">
              Tạo bot với <code>&#64;BotFather</code> rồi dán token vào đây —
              token được lưu mã hóa trong OpenBao. <strong>Polling</strong> chạy
              được sau tường lửa; <strong>Webhook</strong> cần HTTPS công khai.
              @if (!isPublic()) {
                Agent không ở mức <code>public</code> nên bắt buộc nhập mã chat
                được phép.
              }
            </p>
          </div>
        </div>
        <ui-tag [value]="bots().length + ' bot'" severity="secondary" />
      </div>

      <form class="inline-form" (ngSubmit)="createBot()">
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="grow"
        >
          <mat-label>Bot token</mat-label>
          <input
            matInput
            name="tgtoken"
            type="password"
            autocomplete="off"
            [(ngModel)]="botForm.token"
            required
          />
        </mat-form-field>
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="num-field"
        >
          <mat-label>Chế độ</mat-label>
          <mat-select name="tgmode" [(ngModel)]="botForm.mode">
            <mat-option value="polling">Polling</mat-option>
            <mat-option value="webhook">Webhook</mat-option>
          </mat-select>
        </mat-form-field>
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="grow"
        >
          <mat-label>Mã chat được phép (cách bởi dấu phẩy)</mat-label>
          <input
            matInput
            name="tgchats"
            [(ngModel)]="botForm.chats"
            [required]="!isPublic()"
          />
        </mat-form-field>
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="num-field"
        >
          <mat-label>Tin/phút</mat-label>
          <input
            matInput
            type="number"
            name="tgrate"
            [(ngModel)]="botForm.rate"
          />
        </mat-form-field>
        <button
          mat-flat-button
          class="btn-aries"
          type="submit"
          [disabled]="!botForm.token"
        >
          <mat-icon svgIcon="add" /> Kết nối bot
        </button>
      </form>

      @if (bots().length === 0) {
        <p class="muted small empty-row">Chưa kết nối bot Telegram nào.</p>
      } @else {
        <div class="table-wrap">
          <table class="aries-table">
            <thead>
              <tr>
                <th>Bot</th>
                <th>Trạng thái</th>
                <th>Hoạt động</th>
                <th class="act"></th>
              </tr>
            </thead>
            <tbody>
              @for (b of bots(); track b.id) {
                <tr [class.revoked]="!b.enabled">
                  <td>
                    <strong>&#64;{{ b.bot_username }}</strong>
                    <div class="muted small">
                      {{ b.mode === 'webhook' ? 'Webhook' : 'Polling' }} ·
                      {{ b.rate_limit_per_min }}/phút ·
                      {{
                        b.allowed_chat_ids.length
                          ? b.allowed_chat_ids.length + ' chat được phép'
                          : 'mọi người'
                      }}
                    </div>
                  </td>
                  <td>
                    <ui-tag
                      [value]="b.enabled ? 'Đang bật' : 'Đã tắt'"
                      [severity]="b.enabled ? 'success' : 'secondary'"
                    />
                  </td>
                  <td class="muted small">
                    {{
                      b.last_used_at
                        ? 'Dùng ' + (b.last_used_at | date: 'dd/MM HH:mm')
                        : 'Chưa dùng'
                    }}
                  </td>
                  <td class="act">
                    <button
                      mat-stroked-button
                      class="pill-btn"
                      type="button"
                      (click)="editChats(b)"
                    >
                      Chat được phép
                    </button>
                    <button
                      mat-stroked-button
                      class="pill-btn"
                      type="button"
                      (click)="toggleBot(b)"
                    >
                      {{ b.enabled ? 'Tắt' : 'Bật' }}
                    </button>
                    <button
                      mat-stroked-button
                      class="pill-btn danger-btn"
                      type="button"
                      (click)="deleteBot(b)"
                    >
                      Gỡ
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </section>
  `,
  styles: `
    .pub-top {
      margin-bottom: 0.65rem;
    }
    .panel-card {
      margin-bottom: 1.05rem;
    }
    .section-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 0.95rem;
      h2 {
        margin: 0 0 0.15rem;
        font-size: 1.02rem;
      }
      p {
        margin: 0;
        max-width: 52rem;
      }
    }
    .section-head-left {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      min-width: 0;
      flex: 1;
    }
    .inline-form {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: center;
      margin-bottom: 0.85rem;
    }
    .grow {
      flex: 1;
      min-width: 220px;
    }
    .num-field {
      width: 135px;
    }
    tr.revoked {
      opacity: 0.58;
    }
    .act button + button {
      margin-left: 0.35rem;
    }
    .empty-row {
      margin: 0.25rem 0 0;
      padding: 0.85rem 1rem;
      border-radius: var(--app-radius-md);
      background: var(--app-surface-2);
      border: 1px dashed var(--app-border-strong);
      text-align: center;
    }
    .secret-card {
      border-color: var(--app-warn-border);
      box-shadow: var(--app-shadow-md);
    }
    .secret-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 0.75rem;
    }
    .snippet-label {
      margin: 0.75rem 0 0.3rem;
    }
    .copy-row {
      display: flex;
      gap: 0.55rem;
      align-items: center;
      margin: 0.35rem 0;
      flex-wrap: wrap;
      code {
        flex: 1;
        min-width: 200px;
        overflow-x: auto;
        white-space: nowrap;
        padding: 0.5rem 0.7rem;
        background: var(--app-surface-2);
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-md);
        font-size: 0.8rem;
      }
    }
    .code-box {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 0.78rem;
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      padding: 0.75rem 0.85rem;
      border-radius: var(--app-radius-md);
    }
  `,
})
export class AgentPublish implements OnInit {
  readonly id = input.required<string>();

  private readonly api = inject(ChannelsApi);
  private readonly agents = inject(AgentsApi);
  protected readonly apiBase = new URL(
    inject(APP_CONFIG).apiBaseUrl,
    window.location.origin,
  ).href;

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly shares = signal<ShareLinkInfo[]>([]);
  protected readonly keys = signal<ApiKeyInfo[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly copied = signal<string | null>(null);
  protected readonly secret = signal<{
    label: string;
    value: string;
    embed?: string;
    curl?: string;
  } | null>(null);
  protected readonly isPublic = computed(
    () => this.agent()?.current?.spec?.data_classification === 'public',
  );
  protected readonly activeSharesCount = computed(
    () => this.shares().filter((s) => s.enabled).length,
  );
  protected readonly activeKeysCount = computed(
    () => this.keys().filter((k) => !k.revoked_at).length,
  );
  protected readonly activeBotsCount = computed(
    () => this.bots().filter((b) => b.enabled).length,
  );

  protected shareForm = { name: 'Website', origins: '', rate: 10, daily: 1000 };
  protected keyForm = { name: '', rate: 60 };
  protected botForm: {
    token: string;
    mode: 'polling' | 'webhook';
    chats: string;
    rate: number;
  } = {
    token: '',
    mode: 'polling',
    chats: '',
    rate: 20,
  };
  protected readonly bots = signal<TelegramBotInfo[]>([]);

  ngOnInit(): void {
    this.agents.get(this.id()).subscribe({
      next: (a) => this.agent.set(a),
      error: (e) => this.fail(e),
    });
    this.reload();
  }

  protected createShare(): void {
    this.api
      .createShare(this.id(), {
        name: this.shareForm.name,
        allowed_origins: this.shareForm.origins
          .split(',')
          .map((o) => o.trim())
          .filter(Boolean),
        rate_limit_per_min: Number(this.shareForm.rate) || 10,
        daily_message_limit: Number(this.shareForm.daily) || 1000,
      })
      .subscribe({ next: (s) => this.showShare(s), error: (e) => this.fail(e) });
  }

  protected toggleShare(s: ShareLinkInfo): void {
    this.api.updateShare(s.id, { enabled: !s.enabled }).subscribe({
      next: () => this.reload(),
      error: (e) => this.fail(e),
    });
  }

  protected rotateShare(s: ShareLinkInfo): void {
    if (
      !confirm(
        'Tạo link mới? Link cũ (và widget đang nhúng) sẽ ngừng hoạt động ngay.',
      )
    )
      return;
    this.api.rotateShare(s.id).subscribe({
      next: (r) => this.showShare(r),
      error: (e) => this.fail(e),
    });
  }

  protected deleteShare(s: ShareLinkInfo): void {
    if (!confirm(`Xóa link “${s.name}”?`)) return;
    this.api
      .deleteShare(s.id)
      .subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected createKey(): void {
    this.api
      .createApiKey(
        this.id(),
        this.keyForm.name || 'API key',
        Number(this.keyForm.rate) || 60,
      )
      .subscribe({
        next: (k) => {
          this.keyForm.name = '';
          this.secret.set({
            label: 'API key',
            value: k.key ?? '',
            curl:
              `curl -X POST ${this.apiBase}/v1/public/v1/chat \\\n` +
              `  -H "Authorization: Bearer ${k.key}" -H "Content-Type: application/json" \\\n` +
              `  -d '{"message": "Xin chào", "user": "khach-001"}'`,
          });
          this.reload();
        },
        error: (e) => this.fail(e),
      });
  }

  protected revoke(k: ApiKeyInfo): void {
    if (
      !confirm(
        `Thu hồi key “${k.name}”? Hệ thống đang dùng key này sẽ bị từ chối ngay.`,
      )
    )
      return;
    this.api
      .revokeApiKey(k.id)
      .subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected createBot(): void {
    this.api
      .createTelegramBot(this.id(), {
        name: '',
        token: this.botForm.token.trim(),
        mode: this.botForm.mode,
        allowed_chat_ids: parseChats(this.botForm.chats),
        rate_limit_per_min: Number(this.botForm.rate) || 20,
      })
      .subscribe({
        next: () => {
          this.botForm = { ...this.botForm, token: '', chats: '' };
          this.reload();
        },
        error: (e) => {
          this.botForm.token = '';
          this.fail(e);
        },
      });
  }

  protected editChats(b: TelegramBotInfo): void {
    const value = prompt(
      'Mã chat được phép (phẩy). Để trống = mọi người (chỉ agent public):',
      b.allowed_chat_ids.join(', '),
    );
    if (value === null) return;
    this.api
      .updateTelegramBot(b.id, { allowed_chat_ids: parseChats(value) })
      .subscribe({
        next: () => this.reload(),
        error: (e) => this.fail(e),
      });
  }

  protected toggleBot(b: TelegramBotInfo): void {
    this.api.updateTelegramBot(b.id, { enabled: !b.enabled }).subscribe({
      next: () => this.reload(),
      error: (e) => this.fail(e),
    });
  }

  protected deleteBot(b: TelegramBotInfo): void {
    if (
      !confirm(
        `Gỡ bot @${b.bot_username} khỏi agent? Token sẽ bị xóa khỏi kho bí mật.`,
      )
    )
      return;
    this.api
      .deleteTelegramBot(b.id)
      .subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected copy(value: string, label = 'Nội dung'): void {
    void navigator.clipboard?.writeText(value);
    this.copied.set(`Đã sao chép ${label} vào bộ nhớ tạm.`);
    setTimeout(() => this.copied.set(null), 2500);
  }

  private showShare(s: ShareLinkInfo): void {
    const origin = window.location.origin;
    const url = `${origin}/c/${s.token}`;
    this.secret.set({
      label: 'Link chia sẻ',
      value: url,
      embed: `<script src="${origin}/embed.js" data-aries-token="${s.token}" async></script>`,
    });
    this.reload();
  }

  private reload(): void {
    this.api
      .shares(this.id())
      .subscribe({ next: (s) => this.shares.set(s), error: () => undefined });
    this.api.apiKeys(this.id()).subscribe({
      next: (k) => this.keys.set(k),
      error: (e) => this.fail(e),
    });
    this.api
      .telegramBots(this.id())
      .subscribe({ next: (b) => this.bots.set(b), error: () => undefined });
  }

  private fail(err: { error?: { error?: { message?: string } } }): void {
    this.error.set(err.error?.error?.message ?? 'Thao tác thất bại.');
  }
}

function parseChats(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .map((v) => Number(v.trim()))
    .filter((n) => Number.isInteger(n) && n !== 0);
}

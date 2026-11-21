import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
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
    Notice,
    Tag,
  ],
  template: `
    <div class="head">
      <a mat-icon-button [routerLink]="['/agents', id(), 'chat']" aria-label="Quay lại">
        <mat-icon svgIcon="arrow_back" />
      </a>
      <div>
        <h1>Chia sẻ &amp; API — {{ agent()?.name }}</h1>
        <p class="muted small">
          Kênh ngoài chạy bản đã publish, không có người duyệt (thao tác cần duyệt sẽ bị chặn) và
          không dùng được tài nguyên cá nhân. Link công khai không dùng được kết nối/MCP.
        </p>
      </div>
    </div>

    @if (error()) {
      <ui-notice severity="error">{{ error() }}</ui-notice>
    }
    @if (secret(); as s) {
      <div class="secret">
        <ui-notice severity="warn">
          {{ s.label }} chỉ hiện <strong>một lần</strong> — hãy sao chép và lưu lại ngay.
        </ui-notice>
        <div class="copy-row">
          <code>{{ s.value }}</code>
          <button mat-stroked-button type="button" (click)="copy(s.value)">
            <mat-icon svgIcon="content_copy" /> Sao chép
          </button>
        </div>
        @if (s.embed) {
          <p class="small muted">Nhúng vào website (dán trước thẻ đóng &lt;/body&gt;):</p>
          <div class="copy-row">
            <code>{{ s.embed }}</code>
            <button mat-stroked-button type="button" (click)="copy(s.embed)">
              <mat-icon svgIcon="content_copy" /> Sao chép
            </button>
          </div>
        }
        @if (s.curl) {
          <p class="small muted">Gọi thử:</p>
          <pre>{{ s.curl }}</pre>
        }
        <button mat-button type="button" (click)="secret.set(null)">Đã lưu, ẩn đi</button>
      </div>
    }

    <section class="card">
      <h2><mat-icon svgIcon="public" /> Link chia sẻ &amp; widget nhúng</h2>
      @if (!isPublic()) {
        <ui-notice severity="info" [compact]="true">
          Chỉ agent có phân loại dữ liệu “Công khai (public)” mới tạo được link công khai. Đổi ở tab
          Tổng quan rồi publish lại.
        </ui-notice>
      } @else {
        <form class="row" (ngSubmit)="createShare()">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <mat-label>Tên link</mat-label>
            <input matInput name="sname" [(ngModel)]="shareForm.name" required />
          </mat-form-field>
          <mat-form-field appearance="outline" subscriptSizing="dynamic" class="grow">
            <mat-label>Chỉ cho nhúng ở (để trống = mọi nơi)</mat-label>
            <input matInput name="origins" [(ngModel)]="shareForm.origins"
                   placeholder="https://www.congty.vn, https://intranet.congty.vn" />
          </mat-form-field>
          <mat-form-field appearance="outline" subscriptSizing="dynamic" class="num">
            <mat-label>Tin/phút/khách</mat-label>
            <input matInput type="number" name="rate" [(ngModel)]="shareForm.rate" />
          </mat-form-field>
          <mat-form-field appearance="outline" subscriptSizing="dynamic" class="num">
            <mat-label>Tin/ngày</mat-label>
            <input matInput type="number" name="daily" [(ngModel)]="shareForm.daily" />
          </mat-form-field>
          <button mat-flat-button class="btn-aries" type="submit">
            <mat-icon svgIcon="add" /> Tạo link
          </button>
        </form>
      }
      <table>
        <tbody>
          @for (s of shares(); track s.id) {
            <tr>
              <td>
                <strong>{{ s.name }}</strong>
                <div class="muted small"><code>{{ s.prefix }}…</code>
                  · {{ s.rate_limit_per_min }}/phút · {{ s.daily_message_limit }}/ngày
                  @if (s.allowed_origins.length) { · nhúng ở: {{ s.allowed_origins.join(', ') }} }
                </div>
              </td>
              <td>
                <ui-tag [value]="s.enabled ? 'đang bật' : 'đã tắt'"
                        [severity]="s.enabled ? 'success' : 'secondary'" />
              </td>
              <td class="muted small">
                {{ s.last_used_at ? ('dùng ' + (s.last_used_at | date: 'short')) : 'chưa dùng' }}
              </td>
              <td class="actions">
                <button mat-button type="button" (click)="toggleShare(s)">{{ s.enabled ? 'Tắt' : 'Bật' }}</button>
                <button mat-button type="button" (click)="rotateShare(s)">Tạo lại link</button>
                <button mat-icon-button type="button" aria-label="Xóa link" (click)="deleteShare(s)">
                  <mat-icon svgIcon="delete" />
                </button>
              </td>
            </tr>
          } @empty {
            <tr><td class="muted small">Chưa có link nào.</td></tr>
          }
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2><mat-icon svgIcon="key" /> API key</h2>
      <p class="muted small">
        Gọi agent từ hệ thống khác: <code>POST {{ apiBase }}/v1/public/v1/chat</code> với header
        <code>Authorization: Bearer &lt;key&gt;</code>. Truyền <code>user</code> để tách hội thoại
        theo người dùng cuối.
      </p>
      <form class="row" (ngSubmit)="createKey()">
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="grow">
          <mat-label>Tên key (hệ thống dùng)</mat-label>
          <input matInput name="kname" [(ngModel)]="keyForm.name" required />
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="num">
          <mat-label>Yêu cầu/phút</mat-label>
          <input matInput type="number" name="krate" [(ngModel)]="keyForm.rate" />
        </mat-form-field>
        <button mat-flat-button class="btn-aries" type="submit">
          <mat-icon svgIcon="add" /> Tạo key
        </button>
      </form>
      <table>
        <tbody>
          @for (k of keys(); track k.id) {
            <tr [class.revoked]="k.revoked_at">
              <td><strong>{{ k.name }}</strong>
                <div class="muted small"><code>{{ k.prefix }}…</code> · {{ k.rate_limit_per_min }}/phút</div>
              </td>
              <td class="muted small">
                {{ k.revoked_at ? 'đã thu hồi' : (k.last_used_at ? ('dùng ' + (k.last_used_at | date: 'short')) : 'chưa dùng') }}
              </td>
              <td class="actions">
                @if (!k.revoked_at) {
                  <button mat-button type="button" class="danger" (click)="revoke(k)">Thu hồi</button>
                }
              </td>
            </tr>
          } @empty {
            <tr><td class="muted small">Chưa có API key.</td></tr>
          }
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2><mat-icon svgIcon="forum" /> Telegram</h2>
      <p class="muted small">
        Tạo bot với <code>@BotFather</code> rồi dán token vào đây — token được cất trong OpenBao,
        không hiển thị lại. Chỉ nhận chat riêng.
        <strong>Polling</strong> chạy được sau tường lửa;
        <strong>webhook</strong> cần địa chỉ HTTPS công khai.
        @if (!isPublic()) {
          Agent không ở mức <code>public</code> nên phải nhập mã chat được phép (bot sẽ báo mã
          cho người nhắn chưa được cấp quyền).
        }
      </p>
      <form class="row" (ngSubmit)="createBot()">
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="grow">
          <mat-label>Bot token</mat-label>
          <input matInput name="tgtoken" type="password" autocomplete="off" [(ngModel)]="botForm.token" required />
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="num">
          <mat-label>Chế độ</mat-label>
          <select matNativeControl name="tgmode" [(ngModel)]="botForm.mode">
            <option value="polling">Polling</option>
            <option value="webhook">Webhook</option>
          </select>
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="grow">
          <mat-label>Mã chat được phép (phẩy)</mat-label>
          <input matInput name="tgchats" [(ngModel)]="botForm.chats" [required]="!isPublic()" />
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="num">
          <mat-label>Tin/phút/chat</mat-label>
          <input matInput type="number" name="tgrate" [(ngModel)]="botForm.rate" />
        </mat-form-field>
        <button mat-flat-button class="btn-aries" type="submit" [disabled]="!botForm.token">
          <mat-icon svgIcon="add" /> Kết nối bot
        </button>
      </form>
      <table>
        <tbody>
          @for (b of bots(); track b.id) {
            <tr [class.revoked]="!b.enabled">
              <td><strong>&#64;{{ b.bot_username }}</strong>
                <div class="muted small">
                  {{ b.mode === 'webhook' ? 'Webhook' : 'Polling' }} · {{ b.rate_limit_per_min }}/phút ·
                  {{ b.allowed_chat_ids.length ? b.allowed_chat_ids.length + ' chat được phép' : 'mọi người' }}
                </div>
              </td>
              <td class="muted small">
                {{ b.last_used_at ? ('dùng ' + (b.last_used_at | date: 'short')) : 'chưa dùng' }}
              </td>
              <td class="actions">
                <button mat-button type="button" (click)="editChats(b)">Chat được phép</button>
                <button mat-button type="button" (click)="toggleBot(b)">{{ b.enabled ? 'Tắt' : 'Bật' }}</button>
                <button mat-button type="button" class="danger" (click)="deleteBot(b)">Gỡ</button>
              </td>
            </tr>
          } @empty {
            <tr><td class="muted small">Chưa kết nối bot Telegram.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
  styles: `
    .head { display: flex; gap: 0.5rem; align-items: flex-start; margin-bottom: 0.8rem;
      h1 { font-size: 1.2rem; margin: 0.4rem 0 0.2rem; } p { margin: 0; max-width: 60rem; } }
    .card { background: var(--app-surface); border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl); padding: 1rem 1.2rem; margin-bottom: 1rem;
      h2 { display: flex; align-items: center; gap: 0.4rem; font-size: 1.02rem; margin: 0 0 0.7rem; } }
    .row { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; margin-bottom: 0.6rem; }
    .grow { flex: 1; min-width: 240px; }
    .num { width: 130px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    td { padding: 0.5rem 0.4rem; border-top: 1px solid var(--app-border); vertical-align: middle; }
    .actions { text-align: right; white-space: nowrap; }
    tr.revoked { opacity: 0.55; }
    .danger { color: var(--app-danger, #dc2626); }
    .secret { background: var(--app-surface); border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl); padding: 0.9rem 1.1rem; margin-bottom: 1rem; }
    .copy-row { display: flex; gap: 0.5rem; align-items: center; margin: 0.4rem 0;
      code { flex: 1; overflow-x: auto; white-space: nowrap; padding: 0.45rem 0.6rem;
        background: var(--app-surface-2); border-radius: 8px; font-size: 0.8rem; } }
    pre { white-space: pre-wrap; font-size: 0.78rem; background: var(--app-surface-2); padding: 0.6rem;
      border-radius: 8px; }
  `,
})
export class AgentPublish implements OnInit {
  readonly id = input.required<string>();

  private readonly api = inject(ChannelsApi);
  private readonly agents = inject(AgentsApi);
  protected readonly apiBase = new URL(inject(APP_CONFIG).apiBaseUrl, window.location.origin).href;

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly shares = signal<ShareLinkInfo[]>([]);
  protected readonly keys = signal<ApiKeyInfo[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly secret = signal<{
    label: string;
    value: string;
    embed?: string;
    curl?: string;
  } | null>(null);
  protected readonly isPublic = computed(
    () => this.agent()?.current?.spec?.data_classification === 'public',
  );
  protected shareForm = { name: 'Website', origins: '', rate: 10, daily: 1000 };
  protected keyForm = { name: '', rate: 60 };
  protected botForm: { token: string; mode: 'polling' | 'webhook'; chats: string; rate: number } = {
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
        allowed_origins: this.shareForm.origins.split(',').map((o) => o.trim()).filter(Boolean),
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
    if (!confirm('Tạo link mới? Link cũ (và widget đang nhúng) sẽ ngừng hoạt động ngay.')) return;
    this.api.rotateShare(s.id).subscribe({
      next: (r) => this.showShare(r),
      error: (e) => this.fail(e),
    });
  }

  protected deleteShare(s: ShareLinkInfo): void {
    if (!confirm(`Xóa link “${s.name}”?`)) return;
    this.api.deleteShare(s.id).subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected createKey(): void {
    this.api
      .createApiKey(this.id(), this.keyForm.name || 'API key', Number(this.keyForm.rate) || 60)
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
    if (!confirm(`Thu hồi key “${k.name}”? Hệ thống đang dùng key này sẽ bị từ chối ngay.`)) return;
    this.api.revokeApiKey(k.id).subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
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
    const value = prompt('Mã chat được phép (phẩy). Để trống = mọi người (chỉ agent public):',
      b.allowed_chat_ids.join(', '));
    if (value === null) return;
    this.api.updateTelegramBot(b.id, { allowed_chat_ids: parseChats(value) }).subscribe({
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
    if (!confirm(`Gỡ bot @${b.bot_username} khỏi agent? Token sẽ bị xóa khỏi kho bí mật.`)) return;
    this.api.deleteTelegramBot(b.id).subscribe({ next: () => this.reload(), error: (e) => this.fail(e) });
  }

  protected copy(value: string): void {
    void navigator.clipboard?.writeText(value);
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
    this.api.shares(this.id()).subscribe({ next: (s) => this.shares.set(s), error: () => undefined });
    this.api.apiKeys(this.id()).subscribe({ next: (k) => this.keys.set(k), error: (e) => this.fail(e) });
    this.api.telegramBots(this.id()).subscribe({ next: (b) => this.bots.set(b), error: () => undefined });
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

import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import {
  AgentsApi,
  AgentSummary,
  OpsApi,
  OpsChannel,
  OpsFeedback,
  OpsFilter,
  OpsThread,
  OpsTranscript,
  OpsUsage,
  SessionService,
} from '@aiagent/core';
import { Notice, Severity, Tag } from '@aiagent/ui';

type Tab = 'threads' | 'feedback' | 'usage';

const CHANNEL_LABEL: Record<OpsChannel, string> = {
  app: 'Ứng dụng',
  share: 'Link / widget',
  api: 'API',
  telegram: 'Telegram',
};

const CHANNEL_SEVERITY: Record<OpsChannel, Severity> = {
  app: 'primary',
  share: 'info',
  api: 'warn',
  telegram: 'success',
};

interface Group {
  key: string;
  label: string;
  runs: number;
  cost: number;
  tokens: number;
  pct: number;
}

@Component({
  selector: 'app-ops',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIcon,
    MatProgressBarModule,
    MatSelectModule,
    Notice,
    Tag,
  ],
  template: `
    <div class="page-hero">
      <div class="hero-left">
        <span class="head-icon accent" aria-hidden="true">
          <mat-icon class="sm" svgIcon="dashboard" />
        </span>
        <div class="hero-copy">
          <h1>Vận hành</h1>
          <p class="subtitle">
            Theo dõi nhật ký hội thoại đa kênh, phản hồi người dùng và chi phí
            vận hành mô hình theo thời gian thực.
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

    <div class="flow-info-strip">
      <mat-icon class="sm" svgIcon="shield" aria-hidden="true" />
      <span>
        Mọi thao tác mở xem toàn văn hội thoại của người dùng khác đều được hệ
        thống tự động ghi nhận vào nhật ký Audit bất biến.
      </span>
    </div>

    <div class="ops-toolbar">
      <div class="aries-sub-tabs" role="tablist" aria-label="Mục vận hành">
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'threads'"
          [class.active]="tab() === 'threads'"
          (click)="setTab('threads')"
        >
          <mat-icon class="sm" svgIcon="forum" aria-hidden="true" />
          Hội thoại
        </button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'feedback'"
          [class.active]="tab() === 'feedback'"
          (click)="setTab('feedback')"
        >
          <mat-icon class="sm" svgIcon="thumb_up" aria-hidden="true" />
          Phản hồi
        </button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'usage'"
          [class.active]="tab() === 'usage'"
          (click)="setTab('usage')"
        >
          <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
          Chi phí &amp; Token
        </button>
      </div>

      <div class="filter-controls">
        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="filter-field"
        >
          <mat-icon matPrefix class="sm" svgIcon="robot_2" />
          <mat-label>Agent</mat-label>
          <mat-select
            [ngModel]="agentId()"
            (ngModelChange)="setAgent($event)"
            aria-label="Lọc theo agent"
          >
            @if (seeAll()) {
              <mat-option value="">Tất cả agent</mat-option>
            }
            @for (a of agentOptions(); track a.id) {
              <mat-option [value]="a.id">{{ a.name }}</mat-option>
            }
          </mat-select>
        </mat-form-field>

        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="filter-field"
        >
          <mat-icon matPrefix class="sm" svgIcon="public" />
          <mat-label>Kênh</mat-label>
          <mat-select
            [ngModel]="channel()"
            (ngModelChange)="channel.set($event); reload()"
            aria-label="Lọc theo kênh"
          >
            <mat-option value="">Mọi kênh</mat-option>
            @for (c of channels; track c) {
              <mat-option [value]="c">{{ label(c) }}</mat-option>
            }
          </mat-select>
        </mat-form-field>

        <mat-form-field
          appearance="outline"
          subscriptSizing="dynamic"
          class="filter-field compact"
        >
          <mat-icon matPrefix class="sm" svgIcon="refresh" />
          <mat-label>Thời gian</mat-label>
          <mat-select
            [ngModel]="days()"
            (ngModelChange)="days.set(+$event); reload()"
            aria-label="Khoảng thời gian"
          >
            <mat-option [value]="1">24 giờ qua</mat-option>
            <mat-option [value]="7">7 ngày qua</mat-option>
            <mat-option [value]="30">30 ngày qua</mat-option>
            <mat-option [value]="90">90 ngày qua</mat-option>
          </mat-select>
        </mat-form-field>
      </div>
    </div>

    @if (error(); as e) {
      <ui-notice severity="error">{{ e }}</ui-notice>
    }
    @if (!seeAll() && agentOptions().length === 0) {
      <ui-notice severity="info"
        >Bạn chưa sở hữu agent nào để xem nhật ký vận hành.</ui-notice
      >
    }
    @if (loading()) {
      <mat-progress-bar mode="indeterminate" class="ops-progress" />
    }

    @switch (tab()) {
      @case ('threads') {
        <div class="kpi-strip">
          <div class="kpi-card">
            <span class="kpi-icon" aria-hidden="true">
              <mat-icon class="sm" svgIcon="forum" />
            </span>
            <div class="kpi-meta">
              <span class="kpi-val">{{ threads().length | number }}</span>
              <span class="kpi-label">Hội thoại đang hiển thị</span>
            </div>
          </div>
          <div class="kpi-card">
            <span class="kpi-icon info" aria-hidden="true">
              <mat-icon class="sm" svgIcon="description" />
            </span>
            <div class="kpi-meta">
              <span class="kpi-val">{{ threadMessagesTotal() | number }}</span>
              <span class="kpi-label">Tổng tin nhắn</span>
            </div>
          </div>
          <div class="kpi-card">
            <span class="kpi-icon ok" aria-hidden="true">
              <mat-icon class="sm" svgIcon="thumb_up" />
            </span>
            <div class="kpi-meta">
              <span class="kpi-val"
                >{{ threadThumbsUpTotal() }} /
                {{ threadThumbsDownTotal() }}</span
              >
              <span class="kpi-label">Hữu ích / Chưa tốt</span>
            </div>
          </div>
          <div class="kpi-card">
            <span class="kpi-icon warn" aria-hidden="true">
              <mat-icon class="sm" svgIcon="bolt" />
            </span>
            <div class="kpi-meta">
              <span class="kpi-val"
                >\${{ threadCostTotal() | number: '1.2-4' }}</span
              >
              <span class="kpi-label">Chi phí ước tính</span>
            </div>
          </div>
        </div>

        @if (threads().length === 0 && !loading()) {
          <div class="empty-state-card">
            <div class="empty-icon-ring" aria-hidden="true">
              <mat-icon svgIcon="forum" />
            </div>
            <h2>Chưa có hội thoại nào trong khoảng thời gian này</h2>
            <p>
              Hãy thử mở rộng khoảng thời gian, đổi bộ lọc kênh hoặc chọn một
              Agent khác để xem lịch sử tương tác.
            </p>
          </div>
        } @else {
          <div class="split" [class.open]="!!transcript()">
            <section class="panel-card list-panel">
              <div class="panel-header">
                <h2 class="sec-title">
                  <mat-icon class="sm" svgIcon="forum" aria-hidden="true" />
                  <span>Danh sách hội thoại</span>
                </h2>
                <ui-tag
                  [value]="threads().length + ' phiên'"
                  severity="secondary"
                />
              </div>

              <div class="thread-list">
                @for (t of threads(); track t.id) {
                  <button
                    type="button"
                    class="thread-row"
                    [class.sel]="transcript()?.id === t.id"
                    (click)="open(t.id)"
                  >
                    <div class="row-top">
                      <strong class="row-title">{{
                        t.title || '(Hội thoại chưa đặt tiêu đề)'
                      }}</strong>
                      <span class="muted small row-time">{{
                        t.updated_at | date: 'dd/MM HH:mm'
                      }}</span>
                    </div>
                    <div class="row-bottom">
                      <span class="row-sub muted small">
                        <mat-icon class="xs" svgIcon="robot_2" />
                        {{ agentName(t.agent_id) }} · {{ t.user_id }}
                      </span>
                      <div class="row-badges">
                        <ui-tag
                          [value]="label(t.channel)"
                          [severity]="channelSeverity(t.channel)"
                        />
                        <span class="flow-chip chip-read">
                          {{ t.message_count }} tin
                        </span>
                        <span class="flow-chip chip-warn">
                          \${{ t.cost_usd | number: '1.2-4' }}
                        </span>
                        @if (t.thumbs_up) {
                          <span class="rating-pill good">
                            <mat-icon class="xs" svgIcon="thumb_up" />
                            {{ t.thumbs_up }}
                          </span>
                        }
                        @if (t.thumbs_down) {
                          <span class="rating-pill bad">
                            <mat-icon class="xs" svgIcon="thumb_down" />
                            {{ t.thumbs_down }}
                          </span>
                        }
                      </div>
                    </div>
                  </button>
                }
              </div>

              @if (hasMore()) {
                <div class="list-foot">
                  <button
                    mat-stroked-button
                    class="pill-btn"
                    type="button"
                    (click)="loadMore()"
                  >
                    <mat-icon svgIcon="refresh" /> Xem thêm hội thoại
                  </button>
                </div>
              }
            </section>

            @if (transcript(); as tr) {
              <aside
                class="panel-card transcript-panel"
                aria-label="Toàn văn hội thoại"
              >
                <header class="tr-header">
                  <div class="tr-head-copy">
                    <div class="tr-title-row">
                      <strong>{{
                        tr.title || '(Hội thoại chưa đặt tiêu đề)'
                      }}</strong>
                      <ui-tag
                        [value]="'v' + tr.agent_version"
                        severity="secondary"
                      />
                      <ui-tag
                        [value]="label(tr.channel)"
                        [severity]="channelSeverity(tr.channel)"
                      />
                    </div>
                    <div class="muted small">
                      {{ agentName(tr.agent_id) }} · Người dùng:
                      <code>{{ tr.user_id }}</code>
                    </div>
                  </div>
                  <button
                    mat-icon-button
                    type="button"
                    aria-label="Đóng toàn văn"
                    (click)="transcript.set(null)"
                  >
                    <mat-icon svgIcon="close" />
                  </button>
                </header>

                <div class="tr-body">
                  @for (m of tr.messages; track m.id) {
                    <div
                      class="tr-msg"
                      [class.is-user]="m.role === 'user'"
                      [class.is-agent]="m.role !== 'user'"
                    >
                      <div class="tr-who">
                        <span class="tr-role">
                          <mat-icon
                            class="xs"
                            [svgIcon]="m.role === 'user' ? 'person' : 'robot_2'"
                          />
                          {{ m.role === 'user' ? 'Người dùng' : 'Agent' }}
                        </span>
                        <span class="muted small">{{
                          m.created_at | date: 'HH:mm:ss'
                        }}</span>
                      </div>

                      <div class="tr-parts">
                        @for (p of m.parts; track $index) {
                          @switch (p.type) {
                            @case ('text') {
                              <div class="tr-text">{{ p.text }}</div>
                            }
                            @case ('tool_use') {
                              <div class="tr-tool">
                                <div class="tr-tool-head">
                                  <mat-icon
                                    class="xs"
                                    svgIcon="deployed_code"
                                  />
                                  <strong>Gọi tool</strong>
                                  <code>{{ p.name }}</code>
                                </div>
                                <pre class="tr-code">{{ p.input }}</pre>
                              </div>
                            }
                            @case ('tool_result') {
                              <div
                                class="tr-tool result"
                                [class.is-err]="p.is_error"
                              >
                                <div class="tr-tool-head">
                                  <mat-icon
                                    class="xs"
                                    [svgIcon]="
                                      p.is_error ? 'warning' : 'check_circle'
                                    "
                                  />
                                  <strong>{{
                                    p.is_error ? 'Lỗi tool' : 'Kết quả tool'
                                  }}</strong>
                                </div>
                                <pre class="tr-code">{{ p.text }}</pre>
                              </div>
                            }
                          }
                        }
                      </div>

                      @for (f of feedbackFor(tr, m.id); track f.user_id) {
                        <div
                          class="tr-fb"
                          [class.bad]="f.rating < 0"
                          [class.good]="f.rating > 0"
                        >
                          <mat-icon
                            class="xs"
                            [svgIcon]="f.rating > 0 ? 'thumb_up' : 'thumb_down'"
                          />
                          <span>{{
                            f.comment || (f.rating > 0 ? 'Hữu ích' : 'Chưa tốt')
                          }}</span>
                        </div>
                      }
                    </div>
                  }
                </div>
              </aside>
            }
          </div>
        }
      }

      @case ('feedback') {
        <div class="fb-filter-row">
          <div class="aries-sub-tabs" role="tablist" aria-label="Lọc đánh giá">
            <button
              type="button"
              [class.active]="rating() === null"
              (click)="setRating(null)"
            >
              <mat-icon class="sm" svgIcon="dashboard" aria-hidden="true" />
              Tất cả · {{ feedback().length }}
            </button>
            <button
              type="button"
              [class.active]="rating() === -1"
              (click)="setRating(-1)"
            >
              <mat-icon class="sm" svgIcon="thumb_down" aria-hidden="true" />
              Chưa tốt
            </button>
            <button
              type="button"
              [class.active]="rating() === 1"
              (click)="setRating(1)"
            >
              <mat-icon class="sm" svgIcon="thumb_up" aria-hidden="true" />
              Hữu ích
            </button>
          </div>
        </div>

        @if (feedback().length === 0 && !loading()) {
          <div class="empty-state-card">
            <div class="empty-icon-ring" aria-hidden="true">
              <mat-icon svgIcon="thumb_up" />
            </div>
            <h2>Chưa có phản hồi nào</h2>
            <p>
              Đánh giá hữu ích hoặc góp ý từ người dùng cuối trong các phiên
              chat sẽ xuất hiện tại đây để bạn cải thiện prompt và tri thức.
            </p>
          </div>
        } @else {
          <div class="fb-grid">
            @for (f of feedback(); track f.id) {
              <article
                class="panel-card fb-card"
                [class.is-good]="f.rating > 0"
                [class.is-bad]="f.rating < 0"
              >
                <header class="fb-head">
                  <span
                    class="fb-icon"
                    [class.good]="f.rating > 0"
                    [class.bad]="f.rating < 0"
                    aria-hidden="true"
                  >
                    <mat-icon
                      class="sm"
                      [svgIcon]="f.rating > 0 ? 'thumb_up' : 'thumb_down'"
                    />
                  </span>
                  <div class="fb-meta">
                    <div class="fb-meta-top">
                      <strong>{{ agentName(f.agent_id) }}</strong>
                      <ui-tag
                        [value]="label(f.channel)"
                        [severity]="channelSeverity(f.channel)"
                      />
                    </div>
                    <span class="muted small">{{
                      f.created_at | date: 'dd/MM/yyyy HH:mm'
                    }}</span>
                  </div>
                </header>

                @if (f.comment) {
                  <blockquote class="fb-comment">“{{ f.comment }}”</blockquote>
                }

                <div class="fb-answer">
                  <span class="fb-answer-label">Phản hồi của Agent</span>
                  <p>{{ f.answer || '(câu trả lời không có văn bản)' }}</p>
                </div>

                <footer class="fb-foot">
                  <button
                    mat-stroked-button
                    class="pill-btn"
                    type="button"
                    (click)="openFromFeedback(f.thread_id)"
                  >
                    <mat-icon svgIcon="forum" /> Xem toàn văn hội thoại
                  </button>
                </footer>
              </article>
            }
          </div>
        }
      }

      @case ('usage') {
        @if (usage(); as u) {
          <div class="kpi-strip">
            <div class="kpi-card">
              <span class="kpi-icon" aria-hidden="true">
                <mat-icon class="sm" svgIcon="play_arrow" />
              </span>
              <div class="kpi-meta">
                <span class="kpi-val">{{ u.runs | number }}</span>
                <span class="kpi-label">Tổng lượt chạy</span>
              </div>
            </div>
            <div class="kpi-card">
              <span
                class="kpi-icon"
                [class.ok]="u.failed_runs === 0"
                [class.warn]="u.failed_runs > 0"
                aria-hidden="true"
              >
                <mat-icon
                  class="sm"
                  [svgIcon]="u.failed_runs > 0 ? 'warning' : 'check_circle'"
                />
              </span>
              <div class="kpi-meta">
                <span class="kpi-val"
                  >{{
                    u.runs
                      ? ((u.failed_runs / u.runs) * 100 | number: '1.0-1')
                      : 0
                  }}%</span
                >
                <span class="kpi-label"
                  >Tỉ lệ lỗi ({{ u.failed_runs | number }} lượt)</span
                >
              </div>
            </div>
            <div class="kpi-card">
              <span class="kpi-icon info" aria-hidden="true">
                <mat-icon class="sm" svgIcon="database" />
              </span>
              <div class="kpi-meta">
                <span class="kpi-val"
                  >{{ u.input_tokens | number }} /
                  {{ u.output_tokens | number }}</span
                >
                <span class="kpi-label">Token vào / ra</span>
              </div>
            </div>
            <div class="kpi-card">
              <span class="kpi-icon warn" aria-hidden="true">
                <mat-icon class="sm" svgIcon="bolt" />
              </span>
              <div class="kpi-meta">
                <span class="kpi-val"
                  >\${{ u.cost_usd | number: '1.2-4' }}</span
                >
                <span class="kpi-label">Chi phí ước tính</span>
              </div>
            </div>
          </div>

          <section class="panel-card chart-card">
            <div class="panel-header">
              <div>
                <h2 class="sec-title">
                  <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
                  <span>Biểu đồ chi phí theo ngày</span>
                </h2>
                <p class="muted small chart-sub">
                  Di chuột lên từng cột để xem chi phí và số lượt chạy chi tiết
                  của ngày đó.
                </p>
              </div>
              <ui-tag
                [value]="'Tổng: $' + (u.cost_usd | number: '1.2-4')"
                severity="primary"
              />
            </div>

            @if (daily().length === 0) {
              <p class="muted small chart-empty">Chưa có dữ liệu lượt chạy.</p>
            } @else {
              <div
                class="bars-wrap"
                role="img"
                [attr.aria-label]="
                  'Chi phí theo ngày, tổng ' + u.cost_usd + ' USD'
                "
              >
                <div class="bars">
                  @for (d of daily(); track d.day) {
                    <div
                      class="bar-col"
                      [title]="
                        d.day +
                        ': $' +
                        (d.cost | number: '1.2-4') +
                        ' · ' +
                        d.runs +
                        ' lượt'
                      "
                    >
                      <div class="bar-track">
                        <span
                          class="bar-fill"
                          [class.has-val]="d.cost > 0"
                          [style.height.%]="d.pct"
                        ></span>
                      </div>
                      @if (showDateTick($index, daily().length)) {
                        <span class="bar-tick">{{ d.day.slice(5) }}</span>
                      }
                    </div>
                  }
                </div>
              </div>
            }
          </section>

          <div class="groups">
            @for (
              section of [
                {
                  title: 'Phân bổ theo Agent',
                  icon: 'robot_2',
                  rows: byAgent(),
                },
                {
                  title: 'Phân bổ theo Kênh',
                  icon: 'public',
                  rows: byChannel(),
                },
              ];
              track section.title
            ) {
              <section class="panel-card">
                <div class="panel-header">
                  <h2 class="sec-title">
                    <mat-icon
                      class="sm"
                      [svgIcon]="section.icon"
                      aria-hidden="true"
                    />
                    <span>{{ section.title }}</span>
                  </h2>
                  <ui-tag
                    [value]="section.rows.length + ' mục'"
                    severity="secondary"
                  />
                </div>

                @if (section.rows.length === 0) {
                  <p class="muted small table-empty">
                    Chưa có dữ liệu phát sinh.
                  </p>
                } @else {
                  <div class="table-wrap">
                    <table class="aries-table">
                      <thead>
                        <tr>
                          <th>Tên</th>
                          <th class="num">Lượt</th>
                          <th class="num">Token</th>
                          <th class="num">Chi phí ($)</th>
                        </tr>
                      </thead>
                      <tbody>
                        @for (r of section.rows; track r.key) {
                          <tr>
                            <td>
                              <div class="cell-name">
                                <strong>{{ r.label }}</strong>
                                <div class="share-track" aria-hidden="true">
                                  <span
                                    class="share-fill"
                                    [style.width.%]="r.pct"
                                  ></span>
                                </div>
                              </div>
                            </td>
                            <td class="num">{{ r.runs | number }}</td>
                            <td class="num">{{ r.tokens | number }}</td>
                            <td class="num cost-cell">
                              \${{ r.cost | number: '1.2-4' }}
                            </td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }
              </section>
            }
          </div>
        } @else if (!loading()) {
          <div class="empty-state-card">
            <div class="empty-icon-ring" aria-hidden="true">
              <mat-icon svgIcon="bolt" />
            </div>
            <h2>Chưa có dữ liệu chi phí</h2>
            <p>
              Số liệu lượt chạy, token tiêu thụ và chi phí ước tính sẽ hiển thị
              ngay khi các Agent bắt đầu hoạt động.
            </p>
          </div>
        }
      }
    }
  `,
  styles: `
    .ops-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.85rem;
      padding: 0.75rem 1rem;
      margin-bottom: 1.15rem;
      border-radius: var(--app-radius-xl);
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      box-shadow: var(--app-shadow-sm);
    }
    .filter-controls {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      flex-wrap: wrap;
    }
    .filter-field {
      width: 185px;
      &.compact {
        width: 160px;
      }
    }
    .ops-progress {
      border-radius: 999px;
      margin-bottom: 1rem;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 0.9rem;
      h2 {
        margin: 0;
        font-size: 1rem;
      }
    }

    /* Master-detail threads */
    .split {
      display: grid;
      gap: 1rem;
      grid-template-columns: 1fr;
      align-items: start;
      &.open {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
      }
    }
    @media (max-width: 980px) {
      .split.open {
        grid-template-columns: 1fr;
      }
    }
    .list-panel {
      padding: 1rem;
    }
    .thread-list {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }
    .thread-row {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
      width: 100%;
      text-align: left;
      padding: 0.75rem 0.9rem;
      border-radius: var(--app-radius-lg);
      border: 1px solid var(--app-border);
      background: var(--app-surface-2);
      color: var(--app-text);
      font: inherit;
      cursor: pointer;
      transition: all 0.16s ease;
      &:hover {
        border-color: var(--app-border-strong);
        background: var(--app-surface);
        box-shadow: var(--app-shadow-sm);
      }
      &.sel {
        border-color: var(--app-primary);
        background: var(--app-highlight);
        box-shadow: var(--app-shadow-sm);
      }
    }
    .row-top {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.75rem;
    }
    .row-title {
      font-size: 0.92rem;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .row-time {
      flex-shrink: 0;
    }
    .row-bottom {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.55rem;
      flex-wrap: wrap;
    }
    .row-sub {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .row-badges {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      flex-wrap: wrap;
    }
    .rating-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
      padding: 0.12rem 0.45rem;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 600;
      &.good {
        background: var(--app-success-bg);
        color: var(--app-success);
        border: 1px solid var(--app-success-border);
      }
      &.bad {
        background: var(--app-danger-bg);
        color: var(--app-danger);
        border: 1px solid var(--app-danger-border);
      }
    }
    .list-foot {
      display: flex;
      justify-content: center;
      margin-top: 0.9rem;
    }

    /* Transcript drawer */
    .transcript-panel {
      padding: 0;
      overflow: hidden;
      position: sticky;
      top: 1rem;
      max-height: 78vh;
      display: flex;
      flex-direction: column;
    }
    .tr-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.75rem;
      padding: 1rem 1.15rem;
      background: var(--app-surface-2);
      border-bottom: 1px solid var(--app-border);
    }
    .tr-head-copy {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      min-width: 0;
    }
    .tr-title-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
      strong {
        font-size: 0.96rem;
      }
    }
    .tr-body {
      padding: 1rem 1.15rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }
    .tr-msg {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      padding: 0.75rem 0.9rem;
      border-radius: var(--app-radius-lg);
      border: 1px solid var(--app-border);
      background: var(--app-surface-2);
      &.is-user {
        border-left: 3px solid var(--app-primary);
        background: var(--app-highlight);
      }
      &.is-agent {
        border-left: 3px solid var(--app-info);
      }
    }
    .tr-who {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
    }
    .tr-role {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.76rem;
      font-weight: 700;
      color: var(--app-text);
      mat-icon {
        color: var(--app-primary);
      }
    }
    .tr-parts {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }
    .tr-text {
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.88rem;
      line-height: 1.55;
    }
    .tr-tool {
      border-radius: var(--app-radius-md);
      border: 1px solid var(--app-border);
      background: var(--app-surface);
      padding: 0.55rem 0.7rem;
      &.is-err {
        border-color: var(--app-danger-border);
        background: var(--app-danger-bg);
      }
    }
    .tr-tool-head {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.76rem;
      color: var(--app-muted);
      margin-bottom: 0.35rem;
      mat-icon {
        color: var(--app-primary);
      }
    }
    .tr-tool.is-err .tr-tool-head {
      color: var(--app-danger);
      mat-icon {
        color: var(--app-danger);
      }
    }
    .tr-code {
      margin: 0;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 0.76rem;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 12rem;
      overflow: auto;
      color: var(--app-text);
    }
    .tr-fb {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      width: fit-content;
      padding: 0.25rem 0.65rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      margin-top: 0.2rem;
      &.good {
        background: var(--app-success-bg);
        color: var(--app-success);
        border: 1px solid var(--app-success-border);
      }
      &.bad {
        background: var(--app-danger-bg);
        color: var(--app-danger);
        border: 1px solid var(--app-danger-border);
      }
    }

    /* Feedback tab */
    .fb-filter-row {
      margin-bottom: 1rem;
    }
    .fb-grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    }
    .fb-card {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      transition: all 0.18s ease;
      &:hover {
        border-color: var(--app-border-strong);
        box-shadow: var(--app-shadow-md);
        transform: translateY(-2px);
      }
    }
    .fb-head {
      display: flex;
      align-items: center;
      gap: 0.7rem;
    }
    .fb-icon {
      width: 36px;
      height: 36px;
      border-radius: 11px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      &.good {
        background: var(--app-success-bg);
        color: var(--app-success);
        border: 1px solid var(--app-success-border);
      }
      &.bad {
        background: var(--app-danger-bg);
        color: var(--app-danger);
        border: 1px solid var(--app-danger-border);
      }
    }
    .fb-meta {
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
      min-width: 0;
    }
    .fb-meta-top {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
      font-size: 0.9rem;
    }
    .fb-comment {
      margin: 0;
      padding: 0.6rem 0.85rem;
      border-radius: var(--app-radius-md);
      border-left: 3px solid var(--app-primary);
      background: var(--app-highlight);
      font-size: 0.88rem;
      font-weight: 500;
      color: var(--app-text);
    }
    .fb-answer {
      flex: 1;
      padding: 0.65rem 0.85rem;
      border-radius: var(--app-radius-md);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      .fb-answer-label {
        display: block;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--app-muted);
        margin-bottom: 0.25rem;
      }
      p {
        margin: 0;
        font-size: 0.83rem;
        color: var(--app-muted);
        white-space: pre-wrap;
        display: -webkit-box;
        -webkit-line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
    }
    .fb-foot {
      display: flex;
      justify-content: flex-end;
      padding-top: 0.5rem;
      border-top: 1px solid var(--app-border);
    }

    /* Usage tab */
    .chart-card {
      margin-bottom: 1.15rem;
    }
    .chart-sub {
      margin: 0.15rem 0 0;
    }
    .chart-empty,
    .table-empty {
      padding: 1.5rem 0;
      text-align: center;
      margin: 0;
    }
    .bars-wrap {
      padding: 0.75rem 0.5rem 0.25rem;
      border-radius: var(--app-radius-lg);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
    }
    .bars {
      display: flex;
      align-items: stretch;
      gap: 4px;
      height: 170px;
    }
    .bar-col {
      flex: 1;
      min-width: 6px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.35rem;
      cursor: default;
    }
    .bar-track {
      flex: 1;
      width: 100%;
      display: flex;
      align-items: flex-end;
      padding: 0 1px;
    }
    .bar-fill {
      width: 100%;
      min-height: 3px;
      border-radius: 6px 6px 2px 2px;
      background: var(--app-border-strong);
      transition:
        transform 0.15s ease,
        filter 0.15s ease;
      &.has-val {
        background: var(--aries-gradient);
        box-shadow: 0 2px 8px rgba(255, 61, 139, 0.25);
      }
    }
    .bar-col:hover .bar-fill.has-val {
      filter: brightness(1.12);
      transform: scaleY(1.03);
      transform-origin: bottom;
    }
    .bar-tick {
      font-size: 0.68rem;
      font-family: ui-monospace, monospace;
      color: var(--app-muted);
      white-space: nowrap;
    }

    .groups {
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    }
    .cost-cell {
      font-weight: 600;
      color: var(--app-primary);
    }
    .cell-name {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .share-track {
      height: 4px;
      border-radius: 999px;
      background: var(--app-surface-3);
      overflow: hidden;
      max-width: 160px;
    }
    .share-fill {
      display: block;
      height: 100%;
      border-radius: 999px;
      background: var(--aries-gradient);
    }

    @media (max-width: 680px) {
      .filter-field,
      .filter-field.compact {
        width: 100%;
      }
    }
  `,
})
export class Ops implements OnInit {
  private readonly api = inject(OpsApi);
  private readonly agentsApi = inject(AgentsApi);
  private readonly session = inject(SessionService);

  protected readonly channels: OpsChannel[] = [
    'app',
    'share',
    'api',
    'telegram',
  ];
  protected readonly tab = signal<Tab>('threads');
  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly agentId = signal('');
  protected readonly channel = signal<OpsChannel | ''>('');
  protected readonly days = signal(7);
  protected readonly rating = signal<1 | -1 | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly threads = signal<OpsThread[]>([]);
  protected readonly hasMore = signal(false);
  protected readonly transcript = signal<OpsTranscript | null>(null);
  protected readonly feedback = signal<OpsFeedback[]>([]);
  protected readonly usage = signal<OpsUsage | null>(null);

  protected readonly seeAll = computed(() => {
    this.session.me();
    return this.session.hasAnyRole('admin', 'auditor');
  });
  protected readonly agentOptions = computed(() =>
    this.agents().filter(
      (a) => a.status !== 'archived' && (this.seeAll() || a.can_edit),
    ),
  );

  protected readonly threadMessagesTotal = computed(() =>
    this.threads().reduce((sum, t) => sum + t.message_count, 0),
  );
  protected readonly threadThumbsUpTotal = computed(() =>
    this.threads().reduce((sum, t) => sum + (t.thumbs_up || 0), 0),
  );
  protected readonly threadThumbsDownTotal = computed(() =>
    this.threads().reduce((sum, t) => sum + (t.thumbs_down || 0), 0),
  );
  protected readonly threadCostTotal = computed(() =>
    this.threads().reduce((sum, t) => sum + (t.cost_usd || 0), 0),
  );

  protected readonly daily = computed(() => {
    const u = this.usage();
    if (!u) return [];
    const map = new Map<string, { cost: number; runs: number }>();
    const start = new Date(u.since);
    const end = new Date(u.until);
    for (
      let d = new Date(start.toISOString().slice(0, 10));
      d <= end;
      d.setUTCDate(d.getUTCDate() + 1)
    ) {
      map.set(d.toISOString().slice(0, 10), { cost: 0, runs: 0 });
    }
    for (const r of u.rows) {
      const e = map.get(r.day) ?? { cost: 0, runs: 0 };
      e.cost += r.cost_usd;
      e.runs += r.runs;
      map.set(r.day, e);
    }
    const max = Math.max(0, ...[...map.values()].map((v) => v.cost));
    return [...map.entries()].map(([day, v]) => ({
      day,
      ...v,
      pct: max > 0 ? (v.cost / max) * 100 : 0,
    }));
  });
  protected readonly byAgent = computed(() =>
    this.group(
      (r) => r.agent_id,
      (k) => this.agentName(k),
    ),
  );
  protected readonly byChannel = computed(() =>
    this.group(
      (r) => r.channel,
      (k) => this.label(k as OpsChannel),
    ),
  );

  ngOnInit(): void {
    this.agentsApi.list(true).subscribe({
      next: (list) => {
        this.agents.set(list);
        void this.session
          .load()
          .catch(() => undefined)
          .then(() => {
            if (!this.seeAll() && !this.agentId())
              this.agentId.set(this.agentOptions()[0]?.id ?? '');
            this.reload();
          });
      },
      error: () => this.error.set('Không tải được danh sách agent.'),
    });
  }

  protected label(c: OpsChannel): string {
    return CHANNEL_LABEL[c] ?? c;
  }

  protected channelSeverity(c: OpsChannel): Severity {
    return CHANNEL_SEVERITY[c] ?? 'secondary';
  }

  protected agentName(id: string): string {
    return this.agents().find((a) => a.id === id)?.name ?? id.slice(0, 8);
  }

  protected showDateTick(index: number, total: number): boolean {
    if (total <= 10) return true;
    const step = Math.ceil(total / 7);
    return index === 0 || index === total - 1 || index % step === 0;
  }

  protected feedbackFor(tr: OpsTranscript, messageId: number) {
    return tr.feedback.filter((f) => f.message_id === messageId);
  }

  protected setAgent(id: string): void {
    this.agentId.set(id);
    this.transcript.set(null);
    this.reload();
  }

  protected setTab(t: Tab): void {
    this.tab.set(t);
    this.reload();
  }

  protected setRating(r: 1 | -1 | null): void {
    this.rating.set(r);
    this.reload();
  }

  private filter(): OpsFilter {
    const since = new Date(Date.now() - this.days() * 86_400_000).toISOString();
    return {
      agent_id: this.agentId() || null,
      channel: this.channel() || null,
      since,
      rating: this.tab() === 'feedback' ? this.rating() : null,
    };
  }

  protected reload(): void {
    if (!this.seeAll() && !this.agentId()) return;
    this.loading.set(true);
    this.error.set(null);
    const done = {
      complete: () => this.loading.set(false),
      error: (e: unknown) => this.fail(e),
    };
    const f = this.filter();
    switch (this.tab()) {
      case 'threads':
        this.api.threads(f).subscribe({
          next: (t) => {
            this.threads.set(t);
            this.hasMore.set(t.length === 50);
          },
          ...done,
        });
        break;
      case 'feedback':
        this.api
          .feedback(f)
          .subscribe({ next: (x) => this.feedback.set(x), ...done });
        break;
      case 'usage':
        this.api
          .usage(f)
          .subscribe({ next: (x) => this.usage.set(x), ...done });
        break;
    }
  }

  protected loadMore(): void {
    const last = this.threads().at(-1);
    if (!last) return;
    this.api.threads(this.filter(), 50, last.updated_at).subscribe({
      next: (t) => {
        this.threads.update((cur) => [...cur, ...t]);
        this.hasMore.set(t.length === 50);
      },
      error: (e) => this.fail(e),
    });
  }

  protected open(threadId: string): void {
    this.api.transcript(threadId).subscribe({
      next: (t) => this.transcript.set(t),
      error: (e) => this.fail(e),
    });
  }

  protected openFromFeedback(threadId: string): void {
    this.tab.set('threads');
    this.reload();
    this.open(threadId);
  }

  private fail(e: unknown): void {
    this.loading.set(false);
    const detail = (e as { error?: { detail?: unknown } })?.error?.detail;
    this.error.set(
      typeof detail === 'string' ? detail : 'Không tải được dữ liệu vận hành.',
    );
  }

  private group(
    keyOf: (r: OpsUsage['rows'][number]) => string,
    labelOf: (k: string) => string,
  ): Group[] {
    const map = new Map<string, Group>();
    for (const r of this.usage()?.rows ?? []) {
      const k = keyOf(r);
      const g = map.get(k) ?? {
        key: k,
        label: labelOf(k),
        runs: 0,
        cost: 0,
        tokens: 0,
        pct: 0,
      };
      g.runs += r.runs;
      g.cost += r.cost_usd;
      g.tokens += r.input_tokens + r.output_tokens;
      map.set(k, g);
    }
    const items = [...map.values()].sort((a, b) => b.cost - a.cost);
    const maxCost = Math.max(0, ...items.map((x) => x.cost));
    return items.map((x) => ({
      ...x,
      pct: maxCost > 0 ? Math.max(6, (x.cost / maxCost) * 100) : 0,
    }));
  }
}

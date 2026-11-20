import { DatePipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AgentsApi, AgentSummary, SessionService } from '@aiagent/core';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { TagModule } from 'primeng/tag';

@Component({
  selector: 'app-agents-list',
  imports: [RouterLink, ButtonModule, CardModule, TagModule, DatePipe],
  template: `
    <div class="head">
      <h1>Agent</h1>
      @if (canCreate()) {
        <a routerLink="/builder"
          ><p-button label="Tạo bằng mô tả" icon="pi pi-bolt"
        /></a>
      }
      @if (canCreate()) {
        <a routerLink="/agents/new"
          ><p-button
            label="Tạo bằng form"
            icon="pi pi-plus"
            severity="secondary"
        /></a>
      }
    </div>

    @if (loading()) {
      <p class="muted">Đang tải…</p>
    } @else if (agents().length === 0) {
      <p-card>
        <p>
          Chưa có agent nào{{ canCreate() ? '' : ' được chia sẻ với bạn' }}.
        </p>
        @if (canCreate()) {
          <p class="muted">
            Tạo agent đầu tiên bằng mô tả (Agent Architect) hoặc bằng form.
          </p>
        }
      </p-card>
    } @else {
      <div class="grid">
        @for (a of agents(); track a.id) {
          <p-card>
            <ng-template #title>
              <div class="title">
                <span>{{ a.name }}</span>
                <p-tag
                  [value]="statusLabel(a)"
                  [severity]="a.published_version ? 'success' : 'secondary'"
                />
              </div>
            </ng-template>
            <p class="desc">{{ a.description || 'Không có mô tả' }}</p>
            <p class="muted small">
              v{{ a.latest_version }}
              @if (a.published_version) {
                · đang chạy v{{ a.published_version }}
              }
              · cập nhật {{ a.updated_at | date: 'dd/MM HH:mm' }}
            </p>
            <ng-template #footer>
              <div class="actions">
                <a [routerLink]="['/agents', a.id, 'chat']">
                  <p-button label="Chat" icon="pi pi-comments" size="small" />
                </a>
                @if (a.can_edit) {
                  <a [routerLink]="['/agents', a.id, 'edit']">
                    <p-button
                      label="Sửa"
                      icon="pi pi-pencil"
                      size="small"
                      severity="secondary"
                    />
                  </a>
                }
              </div>
            </ng-template>
          </p-card>
        }
      </div>
    }
  `,
  styles: `
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    }
    .title {
      display: flex;
      justify-content: space-between;
      gap: 0.5rem;
      align-items: center;
    }
    .desc {
      min-height: 2.5rem;
    }
    .muted {
      color: var(--p-text-muted-color);
    }
    .small {
      font-size: 0.85rem;
    }
    .actions {
      display: flex;
      gap: 0.5rem;
    }
  `,
})
export class AgentsList implements OnInit {
  private readonly api = inject(AgentsApi);
  private readonly session = inject(SessionService);

  protected readonly agents = signal<AgentSummary[]>([]);
  protected readonly loading = signal(true);

  ngOnInit(): void {
    this.session.load().catch(() => undefined);
    this.api.list().subscribe({
      next: (a) => this.agents.set(a),
      complete: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  protected canCreate(): boolean {
    return this.session.hasAnyRole('builder', 'admin');
  }

  protected statusLabel(a: AgentSummary): string {
    if (a.status === 'archived') return 'Lưu trữ';
    return a.published_version ? 'Đã publish' : 'Nháp';
  }
}

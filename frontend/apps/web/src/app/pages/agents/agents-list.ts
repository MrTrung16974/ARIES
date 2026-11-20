import { DatePipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import { AgentsApi, AgentSummary, SessionService } from '@aiagent/core';
import { Tag } from '@aiagent/ui';

@Component({
  selector: 'app-agents-list',
  imports: [DatePipe, RouterLink, MatButtonModule, MatCardModule, MatIcon, Tag],
  template: `
    <div class="head">
      <h1>Agent</h1>
      @if (canCreate()) {
        <div class="actions">
          <a mat-flat-button routerLink="/builder"
            ><mat-icon svgIcon="bolt" /> Tạo bằng mô tả</a
          >
          <a mat-stroked-button routerLink="/agents/new"
            ><mat-icon svgIcon="add" /> Tạo bằng form</a
          >
        </div>
      }
    </div>

    @if (loading()) {
      <p class="muted">Đang tải…</p>
    } @else if (agents().length === 0) {
      <mat-card appearance="outlined">
        <mat-card-content>
          <p>
            Chưa có agent nào{{ canCreate() ? '' : ' được chia sẻ với bạn' }}.
          </p>
          @if (canCreate()) {
            <p class="muted">
              Tạo agent đầu tiên bằng mô tả (Agent Architect) hoặc bằng form.
            </p>
          }
        </mat-card-content>
      </mat-card>
    } @else {
      <div class="grid">
        @for (a of agents(); track a.id) {
          <mat-card appearance="outlined">
            <mat-card-header>
              <mat-card-title class="title">
                <span>{{ a.name }}</span>
                <ui-tag
                  [value]="statusLabel(a)"
                  [severity]="a.published_version ? 'success' : 'secondary'"
                />
              </mat-card-title>
            </mat-card-header>
            <mat-card-content>
              <p class="desc">{{ a.description || 'Không có mô tả' }}</p>
              <p class="muted small">
                v{{ a.latest_version }}
                @if (a.published_version) {
                  · đang chạy v{{ a.published_version }}
                }
                · cập nhật {{ a.updated_at | date: 'dd/MM HH:mm' }}
              </p>
            </mat-card-content>
            <mat-card-actions>
              <a mat-flat-button [routerLink]="['/agents', a.id, 'chat']">
                <mat-icon svgIcon="forum" /> Chat
              </a>
              @if (a.can_edit) {
                <a mat-button [routerLink]="['/agents', a.id, 'edit']">
                  <mat-icon svgIcon="edit" /> Sửa
                </a>
              }
            </mat-card-actions>
          </mat-card>
        }
      </div>
    }
  `,
  styles: `
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
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
      width: 100%;
    }
    mat-card-header {
      padding-bottom: 0.5rem;
    }
    .desc {
      min-height: 2.5rem;
    }
    .actions {
      display: flex;
      gap: 0.5rem;
    }
    mat-card-actions {
      gap: 0.5rem;
      padding: 0.5rem 1rem 1rem;
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

import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  HostListener,
  inject,
  signal,
  TemplateRef,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  MatDialog,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';
import { BuilderApi, SessionService } from '@aiagent/core';

const SUGGESTIONS = [
  '/agent new ',
  '/agent edit "',
  '/agent list',
  '/agent publish "',
  '/agent chat "',
  '/help',
];

/** Command bar (Ctrl+K): slash command xác định, gõ không có "/" = tạo agent bằng mô tả. */
@Component({
  selector: 'app-command-bar',
  imports: [
    FormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
  ],
  template: `
    <ng-template #tpl>
      <div class="cmd">
        <div class="cmd-header">
          <div class="cmd-brand">
            <mat-icon class="sm" svgIcon="bolt" aria-hidden="true" />
            <span>ARIES Command Bar</span>
          </div>
          <span class="cmd-shortcut">Enter để chạy · Esc để đóng</span>
        </div>

        <mat-form-field
          appearance="outline"
          class="full"
          subscriptSizing="dynamic"
        >
          <mat-icon matPrefix svgIcon="bolt" />
          <input
            matInput
            id="cmd-input"
            [(ngModel)]="text"
            (keydown.enter)="run()"
            placeholder="Mô tả agent cần tạo, hoặc gõ /agent …"
            aria-label="Lệnh"
            autocomplete="off"
          />
        </mat-form-field>

        <div class="hints-wrap">
          <span class="hints-label">Gợi ý lệnh nhanh:</span>
          <div class="hints">
            @for (s of suggestions; track s) {
              <button type="button" (click)="use(s)">
                <code>{{ s.trim() }}</code>
              </button>
            }
          </div>
        </div>

        @if (output()) {
          <pre class="out" [class.err]="isError()">{{ output() }}</pre>
        }
      </div>
    </ng-template>
  `,
  styles: `
    .cmd {
      padding: 1.15rem 1.25rem 1.25rem;
      background: var(--app-surface);
    }
    .cmd-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
      gap: 0.5rem;
    }
    .cmd-brand {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      font-weight: 700;
      font-size: 0.88rem;
      color: var(--app-primary);
    }
    .cmd-shortcut {
      font-size: 0.76rem;
      color: var(--app-muted);
    }
    .hints-wrap {
      margin-top: 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .hints-label {
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--app-muted);
    }
    .hints {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      button {
        border: 1px solid var(--app-border);
        background: var(--app-surface-2);
        border-radius: 999px;
        padding: 0.25rem 0.7rem;
        font: inherit;
        font-size: 0.8rem;
        cursor: pointer;
        color: var(--app-text);
        transition:
          border-color 0.15s ease,
          background 0.15s ease;
        code {
          background: transparent;
          border: none;
          padding: 0;
          font-size: 0.8rem;
        }
        &:hover {
          border-color: var(--app-primary);
          background: var(--app-highlight);
        }
      }
    }
    .out {
      white-space: pre-wrap;
      margin: 0.9rem 0 0;
      padding: 0.75rem 0.9rem;
      border-radius: var(--app-radius-md);
      background: var(--app-surface-2);
      border: 1px solid var(--app-border);
      font-size: 0.84rem;
      &.err {
        color: var(--app-danger);
        background: var(--app-danger-bg);
        border-color: var(--app-danger-border);
      }
    }
  `,
})
export class CommandBar {
  private readonly api = inject(BuilderApi);
  private readonly router = inject(Router);
  private readonly sessionSvc = inject(SessionService);
  private readonly dialog = inject(MatDialog);
  private readonly tpl = viewChild.required<TemplateRef<unknown>>('tpl');
  private ref: MatDialogRef<unknown> | null = null;

  protected readonly output = signal<string | null>(null);
  protected readonly isError = signal(false);
  protected readonly suggestions = SUGGESTIONS;
  protected text = '';

  @HostListener('document:keydown', ['$event'])
  onKey(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      this.toggle();
    }
  }

  toggle(): void {
    if (!this.sessionSvc.hasAnyRole('builder', 'admin')) return;
    if (this.ref) {
      this.ref.close();
      return;
    }
    this.output.set(null);
    this.ref = this.dialog.open(this.tpl(), {
      width: '42rem',
      maxWidth: '95vw',
      autoFocus: '#cmd-input',
      position: { top: '12vh' },
    });
    this.ref.afterClosed().subscribe(() => (this.ref = null));
  }

  protected use(s: string): void {
    this.text = s;
    queueMicrotask(() => document.getElementById('cmd-input')?.focus());
  }

  protected run(): void {
    const text = this.text.trim();
    if (!text) return;
    this.api.command(text).subscribe({
      next: (r) => {
        if (r.kind === 'architect') {
          this.close();
          void this.router.navigate(['/builder'], {
            queryParams: { session: r.session_id, q: r.message },
          });
        } else if (r.kind === 'navigate') {
          this.close();
          void this.router.navigateByUrl(r.url);
        } else {
          this.isError.set(r.kind === 'error');
          this.output.set(r.text);
        }
      },
      error: (e: unknown) => {
        this.isError.set(true);
        this.output.set(
          e instanceof HttpErrorResponse
            ? ((e.error as { error?: { message?: string } })?.error?.message ??
                `Lỗi ${e.status}`)
            : String(e),
        );
      },
    });
  }

  private close(): void {
    this.text = '';
    this.ref?.close();
  }
}

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
            placeholder="Mô tả agent cần tạo, hoặc /agent …  (Esc để đóng)"
            aria-label="Lệnh"
            autocomplete="off"
          />
        </mat-form-field>
        <div class="hints">
          @for (s of suggestions; track s) {
            <button type="button" (click)="use(s)">{{ s.trim() }}</button>
          }
        </div>
        @if (output()) {
          <pre class="out" [class.err]="isError()">{{ output() }}</pre>
        }
      </div>
    </ng-template>
  `,
  styles: `
    .cmd {
      padding: 1rem;
    }
    .hints {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-top: 0.6rem;
      button {
        border: 1px solid var(--app-border);
        background: none;
        border-radius: 999px;
        padding: 0.2rem 0.7rem;
        font: inherit;
        font-size: 0.8rem;
        cursor: pointer;
        color: inherit;
      }
    }
    .out {
      white-space: pre-wrap;
      margin: 0.75rem 0 0;
      font-size: 0.85rem;
      &.err {
        color: var(--app-danger);
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
      width: '40rem',
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

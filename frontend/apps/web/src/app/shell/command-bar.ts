import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  ElementRef,
  HostListener,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { BuilderApi, SessionService } from '@aiagent/core';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';

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
  imports: [DialogModule, FormsModule, InputTextModule],
  template: `
    <p-dialog
      [modal]="true"
      [visible]="open()"
      (visibleChange)="open.set($event)"
      [showHeader]="false"
      [dismissableMask]="true"
      [style]="{ width: '40rem', maxWidth: '95vw' }"
      (onShow)="focus()"
    >
      <div class="bar">
        <i class="pi pi-bolt" aria-hidden="true"></i>
        <input
          #box
          pInputText
          [(ngModel)]="text"
          (keydown.enter)="run()"
          placeholder="Mô tả agent cần tạo, hoặc /agent …  (Esc để đóng)"
          aria-label="Lệnh"
        />
      </div>
      <div class="hints">
        @for (s of suggestions; track s) {
          <button type="button" (click)="use(s)">{{ s.trim() }}</button>
        }
      </div>
      @if (output()) {
        <pre class="out" [class.err]="isError()">{{ output() }}</pre>
      }
    </p-dialog>
  `,
  styles: `
    .bar {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      padding-top: 1rem;
      input {
        flex: 1;
      }
    }
    .hints {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-top: 0.6rem;
      button {
        border: 1px solid var(--p-content-border-color);
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
        color: var(--p-red-600, #dc2626);
      }
    }
  `,
})
export class CommandBar {
  private readonly api = inject(BuilderApi);
  private readonly router = inject(Router);
  private readonly sessionSvc = inject(SessionService);
  private readonly box = viewChild<ElementRef<HTMLInputElement>>('box');

  protected readonly open = signal(false);
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
    this.output.set(null);
    this.open.update((v) => !v);
  }

  protected focus(): void {
    queueMicrotask(() => this.box()?.nativeElement.focus());
  }

  protected use(s: string): void {
    this.text = s;
    this.focus();
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
    this.open.set(false);
  }
}

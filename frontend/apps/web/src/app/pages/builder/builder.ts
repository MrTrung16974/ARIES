import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  computed,
  ElementRef,
  inject,
  input,
  OnDestroy,
  OnInit,
  signal,
  TemplateRef,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import {
  BuilderApi,
  BuilderSessionView,
  ChatStreamError,
  ClarifyQuestion,
  EvalReport,
  Permission,
} from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import {
  MatDialog,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTabsModule } from '@angular/material/tabs';
import { Notice, Tag } from '@aiagent/ui';

export interface BuilderItem {
  role: 'user' | 'architect';
  text: string;
  changes?: string[];
  test?: {
    input: string;
    answer: string;
    tools: string[];
    error: string | null;
  };
  error?: boolean;
}

const RISK_LABEL: Record<string, string> = {
  read: 'đọc',
  write: 'ghi',
  external_send: 'gửi ra ngoài',
  destructive: 'phá hủy',
};

@Component({
  selector: 'app-builder',
  imports: [
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatTabsModule,
    Notice,
    Tag,
  ],
  templateUrl: './builder.html',
  styleUrl: './builder.scss',
})
export class Builder implements OnInit, OnDestroy {
  /** Query: ?session= phiên đã có, ?q= mô tả ban đầu, ?agent= sửa agent có sẵn. */
  readonly session = input<string>();
  readonly q = input<string>();
  readonly agent = input<string>();

  private readonly api = inject(BuilderApi);
  private readonly router = inject(Router);
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');
  private readonly dialog = inject(MatDialog);
  private readonly confirmTpl =
    viewChild.required<TemplateRef<unknown>>('confirmTpl');
  private confirmRef: MatDialogRef<unknown> | null = null;

  protected readonly view = signal<BuilderSessionView | null>(null);
  protected readonly items = signal<BuilderItem[]>([]);
  protected readonly busy = signal(false);
  protected readonly status = signal<string | null>(null);
  protected readonly progress = signal<{ done: number; total: number } | null>(
    null,
  );
  protected readonly questions = signal<ClarifyQuestion[]>([]);
  protected readonly assumptions = signal<string[]>([]);
  protected readonly missing = signal<{ kind: string; description: string }[]>(
    [],
  );
  protected readonly confirmOpen = signal(false);
  protected readonly publishing = signal(false);
  protected readonly published = signal<{
    agent_id: string;
    version: number;
  } | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly cost = signal<number | null>(null);

  protected input = '';
  protected answers: Record<string, string> = {};
  protected override = false;
  protected tabIndex = 0;

  protected readonly spec = computed(() => this.view()?.spec ?? null);
  protected readonly evalReport = computed<EvalReport | null>(
    () => this.view()?.eval ?? null,
  );
  protected readonly permissions = computed<Permission[]>(
    () => this.view()?.permissions ?? [],
  );
  protected readonly specJson = computed(() =>
    JSON.stringify(this.spec(), null, 2),
  );
  protected readonly riskLabel = RISK_LABEL;

  private abort: AbortController | null = null;

  ngOnInit(): void {
    const id = this.session();
    if (id) {
      this.load(id);
      return;
    }
    this.api.createSession(this.agent() ?? null).subscribe({
      next: (s) => {
        this.view.set(s);
        void this.router.navigate(['/builder'], {
          queryParams: { session: s.id },
          replaceUrl: true,
        });
        const first = this.q()?.trim();
        if (first) {
          this.input = first;
          void this.send();
        } else if (s.spec) {
          this.items.set([
            {
              role: 'architect',
              text: `Đang sửa “${s.spec.name}” (v${s.base_version}). Bạn muốn thay đổi gì?`,
            },
          ]);
        }
      },
      error: (e) => this.error.set(this.message(e)),
    });
  }

  ngOnDestroy(): void {
    this.abort?.abort();
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void this.send();
    }
  }

  protected pick(q: ClarifyQuestion, value: string): void {
    this.answers = { ...this.answers, [q.id]: value };
  }

  protected canAnswer(): boolean {
    return this.questions().some((q) => this.answers[q.id]?.trim());
  }

  protected async sendAnswers(): Promise<void> {
    const answered = Object.fromEntries(
      Object.entries(this.answers).filter(([, v]) => v.trim()),
    );
    const text = this.questions()
      .filter((q) => answered[q.id])
      .map((q) => `${q.question} → ${answered[q.id]}`)
      .join('\n');
    this.questions.set([]);
    this.answers = {};
    await this.run({ answers: answered }, text);
  }

  protected async send(): Promise<void> {
    const message = this.input.trim();
    if (!message || this.busy()) return;
    this.input = '';
    await this.run({ message }, message);
  }

  protected openConfirm(): void {
    if (this.confirmRef) return;
    this.override = false;
    this.confirmOpen.set(true);
    this.confirmRef = this.dialog.open(this.confirmTpl(), {
      width: '30rem',
      maxWidth: '95vw',
    });
    this.confirmRef.afterClosed().subscribe(() => {
      this.confirmRef = null;
      this.confirmOpen.set(false);
    });
  }

  protected closeConfirm(): void {
    this.confirmRef?.close();
  }

  protected publish(): void {
    const s = this.view();
    if (!s) return;
    this.publishing.set(true);
    this.api.confirmPublish(s.id, this.override).subscribe({
      next: (r) => {
        this.publishing.set(false);
        this.closeConfirm();
        this.published.set(r);
        this.view.update((v) =>
          v ? { ...v, status: 'published', agent_id: r.agent_id } : v,
        );
        this.push({
          role: 'architect',
          text: `Đã publish version ${r.version}.`,
        });
      },
      error: (e) => {
        this.publishing.set(false);
        this.error.set(this.message(e));
      },
    });
  }

  // ---------------------------------------------------------------- stream
  private async run(
    payload: { message: string } | { answers: Record<string, string> },
    shown: string,
  ): Promise<void> {
    const s = this.view();
    if (!s || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    this.push({ role: 'user', text: shown });
    this.abort = new AbortController();
    try {
      for await (const ev of this.api.send(s.id, payload, this.abort.signal)) {
        const d = ev.data;
        switch (ev.event) {
          case 'status':
            this.status.set(String(d['text'] ?? ''));
            break;
          case 'message':
            this.push({ role: 'architect', text: String(d['text'] ?? '') });
            break;
          case 'clarify':
            this.questions.set(d['questions'] as ClarifyQuestion[]);
            break;
          case 'brief':
            this.assumptions.set((d['assumptions'] as string[]) ?? []);
            break;
          case 'spec_draft':
          case 'spec_diff':
            this.patchSession({
              spec: d['spec'] as BuilderSessionView['spec'],
              permissions: (d['permissions'] as Permission[]) ?? [],
              summary: (d['summary'] as string) ?? null,
            });
            if (ev.event === 'spec_draft') {
              this.missing.set(
                (d['missing'] as { kind: string; description: string }[]) ?? [],
              );
            }
            if ((d['changes'] as string[] | undefined)?.length) {
              this.push({
                role: 'architect',
                text: 'Thay đổi:',
                changes: d['changes'] as string[],
              });
            }
            break;
          case 'validation':
            this.push({
              role: 'architect',
              text: ((d['errors'] as string[]) ?? []).join('; '),
              error: true,
            });
            break;
          case 'eval_progress':
            this.progress.set({
              done: Number(d['done']),
              total: Number(d['total']),
            });
            break;
          case 'eval_result':
            this.patchSession({ eval: d as unknown as EvalReport });
            this.progress.set(null);
            break;
          case 'test_result':
            this.push({
              role: 'architect',
              text: 'Kết quả chạy thử:',
              test: {
                input: String(d['input']),
                answer: String(d['answer'] ?? ''),
                tools: (d['tools_used'] as string[]) ?? [],
                error: (d['error'] as string | null) ?? null,
              },
            });
            break;
          case 'confirm_required':
            this.openConfirm();
            break;
          case 'usage':
            this.cost.set(Number(d['cost_usd'] ?? 0));
            break;
          case 'error':
            this.push({
              role: 'architect',
              text: String(d['message']),
              error: true,
            });
            break;
          case 'done':
            this.patchSession({
              status: d['status'] as BuilderSessionView['status'],
            });
            break;
        }
        this.scrollDown();
      }
    } catch (e) {
      const text =
        e instanceof ChatStreamError
          ? e.message
          : e instanceof DOMException && e.name === 'AbortError'
            ? 'Đã dừng.'
            : 'Mất kết nối tới máy chủ.';
      this.push({ role: 'architect', text, error: true });
    } finally {
      this.status.set(null);
      this.progress.set(null);
      this.busy.set(false);
      this.abort = null;
    }
  }

  protected stop(): void {
    this.abort?.abort();
  }

  private load(id: string): void {
    this.api.session(id).subscribe({
      next: (s) => {
        this.view.set(s);
        this.questions.set(s.status === 'awaiting_user' ? s.questions : []);
        this.items.set(
          (s.messages ?? [])
            .filter((m) => m.content.trim())
            .map((m) => ({ role: m.role, text: m.content })),
        );
        this.scrollDown();
      },
      error: (e) => this.error.set(this.message(e)),
    });
  }

  private patchSession(patch: Partial<BuilderSessionView>): void {
    this.view.update((v) => (v ? { ...v, ...patch } : v));
  }

  private push(item: BuilderItem): void {
    this.items.update((list) => [...list, item]);
    this.scrollDown();
  }

  private scrollDown(): void {
    queueMicrotask(() => {
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) {
      return (
        (e.error as { error?: { message?: string } })?.error?.message ??
        `Lỗi ${e.status}`
      );
    }
    return String(e);
  }
}

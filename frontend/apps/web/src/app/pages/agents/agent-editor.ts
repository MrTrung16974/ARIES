import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  computed,
  inject,
  input,
  OnInit,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import {
  AgentDetail,
  AgentSpec,
  AgentsApi,
  defaultSpec,
  ModelInfo,
  RiskLevel,
  SkillInfo,
} from '@aiagent/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIcon } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTabsModule } from '@angular/material/tabs';
import { Notice, Tag } from '@aiagent/ui';

const TABS = ['overview', 'prompt', 'skills', 'limits', 'json'] as const;

const RISK_LABEL: Record<RiskLevel, string> = {
  read: 'đọc',
  write: 'ghi',
  external_send: 'gửi ra ngoài',
  destructive: 'phá hủy',
};

function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as {
      error?: { message?: string };
      detail?: { loc: unknown[]; msg: string }[];
    };
    if (body?.error?.message) return body.error.message;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((d) => `${d.loc.slice(1).join('.')}: ${d.msg}`)
        .join('; ');
    }
    return `Lỗi ${err.status}`;
  }
  return String(err);
}

@Component({
  selector: 'app-agent-editor',
  imports: [
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatIcon,
    MatInputModule,
    MatSelectModule,
    MatTabsModule,
    Notice,
    Tag,
  ],
  templateUrl: './agent-editor.html',
  styleUrl: './agent-editor.scss',
})
export class AgentEditor implements OnInit {
  readonly id = input<string>();

  private readonly api = inject(AgentsApi);
  private readonly router = inject(Router);

  protected spec: AgentSpec = defaultSpec();
  protected rulesText = '';
  protected startersText = '';
  protected jsonText = '';
  protected note = '';

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly skills = signal<SkillInfo[]>([]);
  protected readonly models = signal<ModelInfo[]>([]);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly info = signal<string | null>(null);
  protected readonly tab = signal<string>('overview');

  protected readonly tabIndex = computed(() =>
    Math.max(0, TABS.indexOf(this.tab() as (typeof TABS)[number])),
  );

  protected readonly isNew = computed(() => !this.id() || this.id() === 'new');
  protected readonly modelOptions = computed(() =>
    this.models().map((m) => ({
      label: `${m.model}${m.available ? '' : ' (chưa cấu hình)'}`,
      value: `${m.provider}:${m.model}`,
      disabled: !m.available,
    })),
  );

  protected readonly effortOptions = [
    'low',
    'medium',
    'high',
    'xhigh',
    'max',
  ].map((v) => ({
    label: v,
    value: v,
  }));
  protected readonly classificationOptions = [
    { label: 'Công khai (public)', value: 'public' },
    { label: 'Nội bộ (internal)', value: 'internal' },
    { label: 'Mật (confidential)', value: 'confidential' },
    { label: 'Hạn chế (restricted) — chỉ model tự host', value: 'restricted' },
  ];
  protected readonly riskLabel = RISK_LABEL;

  ngOnInit(): void {
    this.api.skills().subscribe((s) => this.skills.set(s));
    this.api.models().subscribe((m) => this.models.set(m));
    if (!this.isNew()) {
      this.api.get(this.id() as string).subscribe({
        next: (a) => this.load(a),
        error: (e) => this.error.set(errorMessage(e)),
      });
    } else {
      this.syncText();
    }
  }

  // ---------------------------------------------------------------- model
  protected get modelKey(): string {
    return `${this.spec.model.provider}:${this.spec.model.model}`;
  }

  protected set modelKey(value: string) {
    const [provider, model] = value.split(':');
    this.spec.model.provider = provider as AgentSpec['model']['provider'];
    this.spec.model.model = model;
  }

  // ---------------------------------------------------------------- skills
  protected hasSkill(id: string): boolean {
    return this.spec.skills.some((s) => s.skill_id === id);
  }

  protected toggleSkill(id: string, on: boolean): void {
    this.spec.skills = on
      ? [
          ...this.spec.skills.filter((s) => s.skill_id !== id),
          { skill_id: id, allowed_tools: [], config: {} },
        ]
      : this.spec.skills.filter((s) => s.skill_id !== id);
  }

  protected needsApproval(risk: RiskLevel): boolean {
    return this.spec.approval.require_for.includes(risk);
  }

  // ---------------------------------------------------------------- json
  protected onTab(index: number): void {
    const next = TABS[index] ?? 'overview';
    if (next === 'json') {
      this.applyText();
      this.jsonText = JSON.stringify(this.spec, null, 2);
    }
    this.tab.set(next);
  }

  protected applyJson(): void {
    try {
      this.spec = {
        ...defaultSpec(),
        ...(JSON.parse(this.jsonText) as AgentSpec),
      };
      this.syncText();
      this.error.set(null);
      this.info.set('Đã áp dụng JSON. Bấm Lưu để tạo version mới.');
    } catch (e) {
      this.error.set(`JSON không hợp lệ: ${String(e)}`);
    }
  }

  // ---------------------------------------------------------------- actions
  protected save(): void {
    this.applyText();
    this.saving.set(true);
    this.error.set(null);
    this.info.set(null);
    const done = (): void => this.saving.set(false);

    if (this.isNew()) {
      this.api.create(this.spec, this.note).subscribe({
        next: (a) => {
          done();
          void this.router.navigate(['/agents', a.id, 'edit']);
        },
        error: (e) => {
          done();
          this.error.set(errorMessage(e));
        },
      });
      return;
    }
    const current = this.agent();
    if (!current) return;
    this.api
      .update(current.id, this.spec, current.latest_version, this.note)
      .subscribe({
        next: (v) => {
          done();
          this.note = '';
          this.agent.set({ ...current, latest_version: v.version, current: v });
          this.info.set(`Đã lưu version ${v.version}.`);
        },
        error: (e) => {
          done();
          this.error.set(errorMessage(e));
        },
      });
  }

  protected publish(): void {
    const current = this.agent();
    if (!current) return;
    this.api.publish(current.id, current.latest_version).subscribe({
      next: (a) => {
        this.agent.set({ ...current, ...a });
        this.info.set(`Đã publish version ${a.published_version}.`);
      },
      error: (e) => this.error.set(errorMessage(e)),
    });
  }

  private load(a: AgentDetail): void {
    this.agent.set(a);
    this.spec = structuredClone(a.current.spec);
    this.syncText();
  }

  private syncText(): void {
    this.rulesText = this.spec.rules.join('\n');
    this.startersText = this.spec.starter_prompts.join('\n');
  }

  private applyText(): void {
    const lines = (t: string): string[] =>
      t
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
    this.spec.rules = lines(this.rulesText);
    this.spec.starter_prompts = lines(this.startersText);
  }
}

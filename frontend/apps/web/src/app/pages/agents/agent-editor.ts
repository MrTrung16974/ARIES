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
  AgentSummary,
  ConnectionInfo,
  ConnectionsApi,
  ConnectorInfo,
  KnowledgeApi,
  KnowledgeBaseInfo,
  McpApi,
  McpServerInfo,
  defaultSpec,
  defaultTeam,
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

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Claude',
  openai: 'OpenAI',
  gemini: 'Gemini',
  groq: 'Groq',
  openrouter: 'OpenRouter',
  deepseek: 'DeepSeek',
  ollama: 'Ollama (local)',
  fake: 'Giả lập',
};

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
  private readonly connectionsApi = inject(ConnectionsApi);
  private readonly mcpApi = inject(McpApi);
  private readonly knowledgeApi = inject(KnowledgeApi);
  private readonly router = inject(Router);

  protected spec: AgentSpec = defaultSpec();
  protected rulesText = '';
  protected startersText = '';
  protected jsonText = '';
  protected note = '';

  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly skills = signal<SkillInfo[]>([]);
  protected readonly connections = signal<ConnectionInfo[]>([]);
  protected readonly agents = signal<AgentSummary[]>([]);
  /** Thành viên có thể chọn: agent đã publish, trừ chính nó. */
  protected readonly memberChoices = computed(() =>
    this.agents().filter(
      (a) => a.published_version !== null && a.id !== this.id(),
    ),
  );
  protected readonly connectors = signal<ConnectorInfo[]>([]);
  protected readonly mcpServers = signal<McpServerInfo[]>([]);
  protected readonly knowledgeBases = signal<KnowledgeBaseInfo[]>([]);
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
      label:
        `${PROVIDER_LABEL[m.provider] ?? m.provider} · ${m.model}` +
        (m.available ? '' : ' (chưa cấu hình)'),
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
  protected readonly dlpOptions = [
    { label: 'Che khóa bí mật, CCCD, số thẻ', value: 'mask' },
    { label: 'Che thêm email, số điện thoại', value: 'strict' },
    { label: 'Không che', value: 'off' },
  ];
  protected readonly riskLabel = RISK_LABEL;

  ngOnInit(): void {
    this.api.skills().subscribe((s) => this.skills.set(s));
    this.connectionsApi.list().subscribe((c) => this.connections.set(c));
    this.api.list().subscribe((a) => this.agents.set(a));
    this.connectionsApi.connectors().subscribe((c) => this.connectors.set(c));
    this.mcpApi
      .list()
      .subscribe({
        next: (m) => this.mcpServers.set(m),
        error: () => undefined,
      });
    this.knowledgeApi.list().subscribe({
      next: (k) => this.knowledgeBases.set(k),
      error: () => undefined,
    });
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
    // Id model có thể chứa ':' (Ollama: qwen3:8b) — chỉ tách ở dấu ':' đầu tiên.
    const i = value.indexOf(':');
    this.spec.model.provider = value.slice(
      0,
      i,
    ) as AgentSpec['model']['provider'];
    this.spec.model.model = value.slice(i + 1);
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

  // ---------------------------------------------------------------- kho tri thức
  protected hasKb(id: string): boolean {
    return this.spec.knowledge_bases.includes(id);
  }

  protected toggleKb(id: string, on: boolean): void {
    const others = this.spec.knowledge_bases.filter((k) => k !== id);
    this.spec.knowledge_bases = on ? [...others, id] : others;
  }

  // ---------------------------------------------------------------- MCP
  protected hasMcp(id: string): boolean {
    return this.spec.mcp_servers.some((m) => m.server_id === id);
  }

  protected toggleMcp(id: string, on: boolean): void {
    const others = this.spec.mcp_servers.filter((m) => m.server_id !== id);
    if (!on) {
      this.spec.mcp_servers = others;
      return;
    }
    const server = this.mcpServers().find((s) => s.id === id);
    const read = (server?.tools ?? [])
      .filter((t) => t.risk === 'read')
      .map((t) => t.tool);
    this.spec.mcp_servers = [...others, { server_id: id, allowed_tools: read }];
  }

  protected mcpToolAllowed(id: string, tool: string): boolean {
    const b = this.spec.mcp_servers.find((m) => m.server_id === id);
    return (
      !!b && (b.allowed_tools.length === 0 || b.allowed_tools.includes(tool))
    );
  }

  protected toggleMcpTool(id: string, tool: string, on: boolean): void {
    const b = this.spec.mcp_servers.find((m) => m.server_id === id);
    if (!b) return;
    const all = (this.mcpServers().find((s) => s.id === id)?.tools ?? []).map(
      (t) => t.tool,
    );
    const current = b.allowed_tools.length ? b.allowed_tools : all;
    const next = on
      ? [...new Set([...current, tool])]
      : current.filter((t) => t !== tool);
    b.allowed_tools = next.length === all.length ? [] : next;
    if (!next.length) this.toggleMcp(id, false);
  }

  // ---------------------------------------------------------------- team (Master)
  protected setSupervisor(on: boolean): void {
    this.spec.graph_type = on ? 'supervisor' : 'react';
    this.spec.team = on ? (this.spec.team ?? defaultTeam()) : null;
    if (on) {
      this.spec.skills = [];
      this.spec.connections = [];
      this.spec.mcp_servers = [];
    }
  }

  protected isMember(id: string): boolean {
    return !!this.spec.team?.members.some((m) => m.agent_id === id);
  }

  protected toggleMember(id: string, on: boolean): void {
    const team = this.spec.team;
    if (!team) return;
    const others = team.members.filter((m) => m.agent_id !== id);
    team.members = on ? [...others, { agent_id: id, when_to_use: '' }] : others;
  }

  // ---------------------------------------------------------------- connections
  protected connectorOf(c: ConnectionInfo): ConnectorInfo | undefined {
    return this.connectors().find((x) => x.id === c.connector_id);
  }

  /** Tool của chính kết nối (có cả tool động); API cũ không có thì lấy theo connector. */
  protected connectionTools(c: ConnectionInfo): ConnectorInfo['tools'] {
    return c.tools ?? this.connectorOf(c)?.tools ?? [];
  }

  /** Kết nối hiển thị: được dùng, hoặc đã gắn sẵn trong spec (vẫn giữ được). */
  protected readonly connectionChoices = computed(() => this.connections());

  protected hasConnection(id: string): boolean {
    return this.spec.connections.some((c) => c.connection_id === id);
  }

  protected toggleConnection(c: ConnectionInfo, on: boolean): void {
    const others = this.spec.connections.filter(
      (x) => x.connection_id !== c.id,
    );
    if (!on) {
      this.spec.connections = others;
      return;
    }
    // Mặc định chỉ tool đọc; tool ghi/phá hủy phải chủ động bật (và vẫn cần duyệt).
    const read = this.connectionTools(c)
      .filter((t) => t.risk === 'read')
      .map((t) => t.name);
    this.spec.connections = [
      ...others,
      { connection_id: c.id, allowed_tools: read },
    ];
  }

  protected connToolAllowed(connectionId: string, tool: string): boolean {
    const b = this.spec.connections.find(
      (x) => x.connection_id === connectionId,
    );
    return (
      !!b && (b.allowed_tools.length === 0 || b.allowed_tools.includes(tool))
    );
  }

  protected toggleConnTool(c: ConnectionInfo, tool: string, on: boolean): void {
    const b = this.spec.connections.find((x) => x.connection_id === c.id);
    if (!b) return;
    const all = this.connectionTools(c).map((t) => t.name);
    const current = b.allowed_tools.length ? b.allowed_tools : all;
    const next = on
      ? [...new Set([...current, tool])]
      : current.filter((t) => t !== tool);
    b.allowed_tools = next.length === all.length ? [] : next;
    if (!next.length) this.toggleConnection(c, false);
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

  protected copyJson(): void {
    void navigator.clipboard?.writeText(this.jsonText);
    this.info.set('Đã sao chép JSON đặc tả vào bộ nhớ tạm.');
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
    this.spec = { dlp: 'mask', ...structuredClone(a.current.spec) };
    this.spec.connections ??= [];
    this.spec.mcp_servers ??= [];
    this.spec.when_to_use ??= '';
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

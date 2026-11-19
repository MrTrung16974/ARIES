// Phase 1 sẽ thay bằng client sinh tự động từ OpenAPI (packages/api-client).

export type Role =
  'owner' | 'admin' | 'builder' | 'user' | 'viewer' | 'auditor';

export interface Me {
  subject: string;
  tenant_id: string;
  username: string | null;
  email: string | null;
  roles: Role[];
  created_at: string;
  last_seen_at: string;
}

export interface SystemInfo {
  name: string;
  version: string;
  env: string;
}

export interface ToolSpec {
  name: string;
  description: string;
  risk: 'read' | 'write' | 'external_send' | 'destructive';
}

export interface PluginInfo {
  entry_point: string;
  status: 'active' | 'disabled' | 'failed';
  reason: string;
  id: string | null;
  kind: string | null;
  name: string | null;
  version: string | null;
  tools: ToolSpec[];
}

export interface AuditEvent {
  seq: number;
  event_id: string;
  occurred_at: string;
  actor_id: string | null;
  action: string;
  outcome: 'success' | 'denied' | 'failure';
  resource_type: string | null;
  resource_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
}

export interface ChainStatus {
  intact: boolean;
  records_checked: number;
  broken_at_seq: number | null;
}

// ---------------------------------------------------------------- agents (AgentSpec v2)
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export type RiskLevel = ToolSpec['risk'];
export type DataClassification =
  'public' | 'internal' | 'confidential' | 'restricted';

export interface SkillBinding {
  skill_id: string;
  allowed_tools: string[];
  config: Record<string, unknown>;
}

export interface AgentSpec {
  schema_version: 2;
  name: string;
  description: string;
  graph_type: 'react';
  model: {
    provider: 'anthropic' | 'fake';
    model: string;
    effort: Effort;
    max_tokens: number;
  };
  persona: string;
  instructions: string;
  rules: string[];
  output_format: { language: string; style: string };
  opening_statement: string;
  starter_prompts: string[];
  skills: SkillBinding[];
  knowledge_bases: string[];
  limits: {
    max_iterations: number;
    timeout_s: number;
    max_tokens_per_run: number;
  };
  approval: { require_for: RiskLevel[] };
  data_classification: DataClassification;
}

export interface AgentSummary {
  id: string;
  name: string;
  description: string;
  status: 'draft' | 'published' | 'archived';
  owner_id: string;
  latest_version: number;
  published_version: number | null;
  can_edit: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentVersion {
  version: number;
  spec: AgentSpec;
  created_by: string;
  created_via: string;
  change_note: string;
  created_at: string;
}

export interface AgentDetail extends AgentSummary {
  current: AgentVersion;
}

export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  tools: (ToolSpec & { input_schema: Record<string, unknown> })[];
  ui_hints: Record<string, unknown>;
}

export interface ModelInfo {
  provider: 'anthropic' | 'fake';
  model: string;
  available: boolean;
}

// ---------------------------------------------------------------- conversations
export interface ThreadInfo {
  id: string;
  agent_id: string;
  agent_version: number;
  is_draft: boolean;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ContentBlock {
  type: string;
  [key: string]: unknown;
}

export interface StoredMessage {
  id: number;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  created_at: string;
}

export type RunEventName =
  | 'run_started'
  | 'token'
  | 'thinking'
  | 'tool_start'
  | 'tool_end'
  | 'usage'
  | 'error'
  | 'done';

export interface RunEventMessage {
  event: RunEventName;
  data: Record<string, unknown>;
}

export function defaultSpec(): AgentSpec {
  return {
    schema_version: 2,
    name: '',
    description: '',
    graph_type: 'react',
    model: {
      provider: 'anthropic',
      model: 'claude-opus-5-5',
      effort: 'medium',
      max_tokens: 16000,
    },
    persona: '',
    instructions: '',
    rules: [],
    output_format: { language: 'vi', style: '' },
    opening_statement: '',
    starter_prompts: [],
    skills: [],
    knowledge_bases: [],
    limits: { max_iterations: 10, timeout_s: 120, max_tokens_per_run: 200000 },
    approval: { require_for: ['destructive', 'external_send'] },
    data_classification: 'internal',
  };
}

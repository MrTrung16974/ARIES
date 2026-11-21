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

export interface McpBinding {
  server_id: string;
  allowed_tools: string[];
}

export interface ConnectionBinding {
  connection_id: string;
  allowed_tools: string[];
}

export interface TeamSettings {
  mode: 'auto' | 'fixed';
  members: { agent_id: string; when_to_use: string }[];
  exclude: string[];
  max_delegations_per_run: number;
  max_parallel: number;
}

export interface AgentSpec {
  schema_version: 2;
  name: string;
  description: string;
  graph_type: 'react' | 'supervisor';
  when_to_use: string;
  team?: TeamSettings | null;
  model: {
    provider: ModelProvider;
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
  connections: ConnectionBinding[];
  mcp_servers: McpBinding[];
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

export type ModelProvider =
  'anthropic' | 'gemini' | 'groq' | 'openrouter' | 'ollama' | 'fake';

export interface ModelInfo {
  provider: ModelProvider;
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
  | 'approval_required'
  | 'delegation_start'
  | 'delegation_progress'
  | 'delegation_end'
  | 'usage'
  | 'error'
  | 'done';

export interface RunEventMessage {
  event: RunEventName | (string & {});
  data: Record<string, unknown>;
}

export function defaultSpec(): AgentSpec {
  return {
    schema_version: 2,
    name: '',
    description: '',
    graph_type: 'react',
    when_to_use: '',
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
    connections: [],
    mcp_servers: [],
    knowledge_bases: [],
    limits: { max_iterations: 10, timeout_s: 120, max_tokens_per_run: 200000 },
    approval: { require_for: ['destructive', 'external_send'] },
    data_classification: 'internal',
  };
}

// ---------------------------------------------------------------- Agent Architect
export interface ClarifyQuestion {
  id: string;
  question: string;
  options: { label: string; value: string }[];
}

export interface Permission {
  skill: string;
  connection?: string;
  tool: string;
  risk: RiskLevel;
  status: 'active' | 'needs_approval';
}

export interface EvalCaseResult {
  case: {
    id: string;
    kind: 'happy' | 'edge' | 'rule';
    input: string;
    expectation: string;
  };
  answer: string;
  tools_used: string[];
  passed: boolean;
  score: number;
  reason: string;
  error: string | null;
}

export interface EvalReport {
  score: number;
  passed: boolean;
  threshold: number;
  results: EvalCaseResult[];
  summary?: string;
}

export interface BuilderSessionView {
  id: string;
  status: 'drafting' | 'awaiting_user' | 'ready' | 'published';
  agent_id: string | null;
  base_version: number | null;
  brief: Record<string, unknown> | null;
  questions: ClarifyQuestion[];
  eval: EvalReport | null;
  spec: AgentSpec | null;
  summary: string | null;
  permissions: Permission[];
  brain: string;
  messages?: {
    role: 'user' | 'architect';
    content: string;
    created_at: string;
  }[];
}

export interface BuilderSessionSummary {
  id: string;
  status: BuilderSessionView['status'];
  name: string;
  agent_id: string | null;
  updated_at: string;
}

export type CommandResult =
  | { kind: 'architect'; session_id: string; message: string }
  | { kind: 'navigate'; url: string }
  | { kind: 'result'; text: string }
  | { kind: 'error'; text: string };

// ---------------------------------------------------------------- connections + HITL (Phase 3a)
export interface ConnectorInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  config_schema: JsonSchema;
  secrets: string[];
  tools: ToolSpec[];
  ui_hints: Record<string, unknown>;
  oauth_provider: string | null;
}

export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: unknown[];
  default?: unknown;
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
}

export type ConnectionScope = 'personal' | 'workspace';

export interface ConnectionInfo {
  id: string;
  name: string;
  connector_id: string;
  scope: ConnectionScope;
  owner_id: string;
  config: Record<string, unknown>;
  secret_keys: string[];
  status: 'active' | 'disabled';
  last_test_ok: boolean | null;
  last_test_note: string | null;
  last_test_at: string | null;
  created_at: string;
  updated_at: string;
  can_manage: boolean;
  can_use: boolean;
}

export interface CreateConnection {
  name: string;
  connector_id: string;
  scope: ConnectionScope;
  config: Record<string, unknown>;
  secrets: Record<string, string>;
}

export interface UpdateConnection {
  name?: string;
  config?: Record<string, unknown>;
  secrets?: Record<string, string>;
  enabled?: boolean;
}

export type ApprovalStatus = 'pending' | 'approved' | 'denied' | 'expired';

export interface Approval {
  id: string;
  thread_id: string;
  run_id: string;
  agent_id: string;
  requester_id: string;
  tool_use_id: string;
  tool: string;
  risk: RiskLevel;
  reason: string;
  arguments: Record<string, unknown>;
  approver_roles: string[];
  connection_id: string | null;
  status: ApprovalStatus;
  decided_by: string | null;
  decision_note: string | null;
  created_at: string;
  decided_at: string | null;
  can_decide: boolean;
}

/** Một mục trong sự kiện `approval_required` (SSE). */
export interface ApprovalRequest {
  approval_id: string;
  tool_use_id: string;
  api_name: string;
  tool: string;
  risk: RiskLevel;
  reason: string;
  approver_roles: string[];
  connection_id: string | null;
  arguments: Record<string, unknown>;
}

// ---------------------------------------------------------------- Master agent (Phase 3c)
export interface MasterInfo {
  agent_id: string;
  name: string;
  members: number;
}

export interface PendingState {
  waiting: boolean;
  can_resume: boolean;
  approvals: Approval[];
}

export function defaultTeam(): TeamSettings {
  return {
    mode: 'auto',
    members: [],
    exclude: [],
    max_delegations_per_run: 8,
    max_parallel: 3,
  };
}

// ---------------------------------------------------------------- Phase 3b: MCP + OAuth
export interface OAuthProviderInfo {
  id: string;
  name: string;
}

export interface McpToolInfo {
  name: string;
  tool: string;
  description: string;
  risk: RiskLevel;
  detected_risk: RiskLevel;
}

export interface McpServerInfo {
  id: string;
  name: string;
  slug: string;
  transport: 'http' | 'stdio';
  url: string | null;
  command_key: string | null;
  secret_keys: string[];
  enabled: boolean;
  tools: McpToolInfo[];
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateMcpServer {
  name: string;
  slug: string;
  transport: 'http' | 'stdio';
  url?: string | null;
  command_key?: string | null;
  secrets: Record<string, string>;
}

// ---------------------------------------------------------------- Knowledge (Phase 4)
export interface KnowledgeBaseInfo {
  id: string;
  name: string;
  description: string;
  scope: 'personal' | 'workspace';
  owner_id: string;
  data_classification: DataClassification;
  doc_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
  can_manage: boolean;
  can_read: boolean;
}

export interface KnowledgeDocument {
  id: string;
  kb_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: 'pending' | 'processing' | 'ready' | 'failed';
  error: string | null;
  title: string;
  chunk_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  n: number;
  title: string;
  filename: string;
  page: number | null;
  heading: string;
  document_id: string;
  kb_id: string;
}

export interface KnowledgeSearchResult {
  text: string;
  citations: Citation[];
  scores: number[];
}

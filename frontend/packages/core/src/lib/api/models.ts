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
  dlp?: 'off' | 'mask' | 'strict';
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
  | 'anthropic'
  | 'openai'
  | 'gemini'
  | 'groq'
  | 'openrouter'
  | 'deepseek'
  | 'ollama'
  | 'fake';

export interface ModelInfo {
  provider: ModelProvider;
  model: string;
  available: boolean;
}

export interface ModelProviderInfo {
  id: ModelProvider;
  name: string;
  vendor: string;
  description: string;
  kind: 'cloud' | 'local';
  requires_api_key: boolean;
  requires_base_url: boolean;
  default_base_url: string;
  docs_url: string;
  sample_models: string[];
  supports_free_only: boolean;
  configured: boolean;
  enabled: boolean;
  source: 'db' | 'env' | 'none';
  base_url: string;
  has_api_key: boolean;
  custom_models: string[];
  available_models: string[];
  extra: Record<string, unknown>;
  updated_by: string | null;
  updated_at: string | null;
}

export interface ConfigureModelProviderPayload {
  enabled: boolean;
  base_url?: string | null;
  api_key?: string | null;
  models?: string[];
  extra?: Record<string, unknown>;
}

export interface TestModelProviderPayload {
  base_url?: string | null;
  api_key?: string | null;
  extra?: Record<string, unknown>;
}

export interface ModelProviderProbeResult {
  ok: boolean;
  latency_ms: number;
  models: string[];
  error: string | null;
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
    dlp: 'mask',
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

// ---------------------------------------------------------------- Publish (Phase 5)
export interface ApiKeyInfo {
  id: string;
  agent_id: string;
  name: string;
  prefix: string;
  rate_limit_per_min: number;
  created_by: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  key?: string; // chỉ có lúc vừa tạo
}

export interface ShareLinkInfo {
  id: string;
  agent_id: string;
  name: string;
  prefix: string;
  enabled: boolean;
  allowed_origins: string[];
  rate_limit_per_min: number;
  daily_message_limit: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  token?: string; // chỉ có lúc vừa tạo / tạo lại
}

// ---------------------------------------------------------------- Vận hành (ADR-0020)
export type OpsChannel = 'app' | 'share' | 'api' | 'telegram';

export interface OpsFilter {
  agent_id?: string | null;
  channel?: OpsChannel | null;
  rating?: 1 | -1 | null;
  since?: string | null;
  until?: string | null;
}

export interface OpsThread {
  id: string;
  agent_id: string;
  user_id: string;
  channel: OpsChannel;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  cost_usd: number;
  thumbs_up: number;
  thumbs_down: number;
}

export interface OpsPart {
  type: 'text' | 'tool_use' | 'tool_result';
  text?: string;
  id?: string;
  name?: string;
  input?: string;
  tool_use_id?: string;
  is_error?: boolean;
}

export interface OpsTranscript {
  id: string;
  agent_id: string;
  agent_version: number;
  user_id: string;
  channel: OpsChannel;
  title: string;
  created_at: string;
  messages: {
    id: number;
    role: 'user' | 'assistant';
    created_at: string;
    parts: OpsPart[];
  }[];
  feedback: {
    message_id: number;
    user_id: string;
    rating: 1 | -1;
    comment: string;
  }[];
}

export interface OpsFeedback {
  id: string;
  thread_id: string;
  message_id: number;
  agent_id: string;
  user_id: string;
  channel: OpsChannel;
  rating: 1 | -1;
  comment: string;
  created_at: string;
  answer: string;
}

export interface OpsUsageRow {
  day: string;
  agent_id: string;
  channel: OpsChannel;
  runs: number;
  failed_runs: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface OpsUsage {
  since: string;
  until: string;
  runs: number;
  failed_runs: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  rows: OpsUsageRow[];
}

export interface TelegramBotInfo {
  id: string;
  agent_id: string;
  name: string;
  bot_username: string;
  mode: 'polling' | 'webhook';
  enabled: boolean;
  allowed_chat_ids: number[];
  rate_limit_per_min: number;
  created_by: string;
  created_at: string;
  last_used_at: string | null;
  webhook_url: string | null;
}

// ---------------------------------------------------------------- Tự động hóa (ADR-0021)
export type ScheduleKind = 'cron' | 'gmail';

export interface ScheduleInfo {
  id: string;
  owner_id: string;
  agent_id: string;
  name: string;
  kind: ScheduleKind;
  cron: string;
  timezone: string;
  prompt: string;
  connection_id: string | null;
  trigger_config: { from_contains?: string; subject_contains?: string };
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  failure_count: number;
  created_at: string;
}

export interface CreateSchedule {
  name: string;
  agent_id: string;
  kind: ScheduleKind;
  prompt: string;
  cron?: string;
  timezone?: string;
  interval_minutes?: number;
  connection_id?: string | null;
  trigger_config?: { from_contains?: string; subject_contains?: string };
}

export interface ScheduleRunInfo {
  id: string;
  thread_id: string | null;
  status: string;
  trigger_ref: string;
  title: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** Kết quả gọi thử một model của provider. */
export interface ModelCheckResult {
  model: string;
  ok: boolean;
  error: string | null;
  latency_ms: number | null;
}

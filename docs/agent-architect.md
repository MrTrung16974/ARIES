# Agent Architect — tạo agent bằng ngôn ngữ tự nhiên

Thay cho workflow canvas kiểu Dify (ADR-0013): người dùng mô tả agent bằng lời, Architect dựng
AgentSpec hợp lệ, tự chạy thử, cho sửa tiếp bằng lời; publish luôn cần xác nhận.

## 1. Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Hỏi lại | Tối đa **3 câu/lượt**, chỉ khi thiếu thông tin làm đổi cấu trúc agent; chi tiết nhỏ tự giả định và ghi rõ |
| Eval trước publish | Chạy bắt buộc; **chưa đạt vẫn publish được** nếu người publish xác nhận lần hai (ghi audit) |
| Ai được dùng | **Builder trở lên** (Owner luôn được). Agent cá nhân cho User: sau MVP |
| Bề mặt | Chat ngôn ngữ tự nhiên + slash command trên web (Phase 2); CLI ở Phase 7 |

## 2. Tiêu chí thành công (eval của chính Architect)

| Chỉ số | Mục tiêu |
|---|---|
| Spec hợp lệ ngay lần đầu | ≥ 95% |
| Chọn đúng skill/KB | ≥ 85% |
| Số câu hỏi lại trung bình | ≤ 2 |
| Mô tả → bản nháp đã chạy thử | ≤ 90 giây |
| Lệnh sửa chỉ đổi đúng phần | ≥ 90% |
| Spec cấp quyền vượt người tạo | 0 (chặn cứng) |

## 3. Intent

`create` · `edit` · `explain` · `test` · `publish` / `rollback` (cần xác nhận) · `connect` · `out_of_scope`.
Phân loại bằng `claude-haiku-5-5`.

## 4. Pipeline (LangGraph)

```
classify → understand (Requirement Brief) → clarify? (interrupt, ≤3 câu có lựa chọn)
        → map capabilities (enum động từ catalog đã lọc theo quyền; thiếu → "missing", không bịa)
        → compose spec (structured output theo JSON Schema AgentSpec)
        → validate (code: schema, tham chiếu, quyền, policy, limits; tự sửa ≤ 2 vòng)
        → eval (6–10 case: happy / biên / phá rule; tool ghi-gửi chạy dry-run; judge theo rubric)
        → present (tóm tắt, quyền + risk, giả định, còn thiếu, điểm, diff)
edit:   locate path → JSON Patch (chỉ trên path đã xác định) → validate → chạy lại eval_suite → diff
```

Requirement Brief: `goal, audience, language, tone, capabilities_needed, data_sources, actions,
channels, constraints, schedule, unknowns`.

Khung prompt của agent: `persona · instructions · tool_guidance · rules · output_format ·
opening_statement · starter_prompts`; mặc định an toàn theo `audience` (khách bên ngoài chặt hơn).

## 5. Tool của Architect

| Tool | Loại |
|---|---|
| `catalog.list/describe` (lọc theo quyền) | read |
| `spec.get/propose/patch` (chỉ ghi vào nháp) | write nháp |
| `spec.validate` (code) | read |
| `eval.generate/run` (sandbox) | read |
| `kb.create`, `connection.request` (trả nút OAuth, không tự kết nối) | write |
| `agent.publish/rollback` | **interrupt → xác nhận** |

Architect không thấy secret, không sửa được policy/limit của tenant, không tắt được duyệt tool rủi ro.

## 6. Model

| Bước | Model | Effort |
|---|---|---|
| classify | `claude-haiku-5-5` | low |
| understand / map / compose / patch | `claude-opus-5-5` | medium |
| sinh eval case, judge | `claude-sonnet-5-5` | medium / low |
| chạy agent nháp | theo spec | theo spec |

Prompt caching: system prompt + catalog ổn định ở đầu. Ước tính một lần tạo ~$0,3–0,8, một lần sửa
~$0,1–0,3 (đo thật ở Phase 2). Quota Architect tách riêng quota chạy agent.

## 7. Dữ liệu

```
builder_sessions (tenant_id, user_id, agent_id?, status, brief, draft_spec, base_version)
builder_messages (session_id, seq, role, content, events)
agent_versions   (+ change_note, diff, created_via: architect|form|api|cli, eval_run_id)
eval_suites (agent_id, cases, rubric, version) · eval_runs (target, results, score, cost)
```

## 8. API & SSE

```
POST /api/v1/builder/sessions                {message, agent_id?}
POST /api/v1/builder/sessions/{id}/messages  {message} | {answer, choice}
GET  /api/v1/builder/sessions/{id}/stream    SSE
POST /api/v1/builder/sessions/{id}/confirm   {action: publish|rollback, version?, override_eval?}
```

Sự kiện: `token, thinking_update, clarify, brief, spec_draft, spec_diff, validation,
eval_progress, eval_result, confirm_required, done, error`.

## 9. Giao diện

Hai cột: chat với Architect (câu hỏi làm rõ là nút bấm) | preview agent live (quyền, KB, rule,
điểm eval, tab Tổng quan/Prompt/JSON, nút Thử chat · Sửa tay · Publish). Trang chủ có ô
"Bạn muốn tạo agent gì?" và template gợi ý.

## 10. An toàn

Enum catalog + validate bằng code (chống bịa); catalog và validate theo quyền người tạo (chống leo
thang); validate ép `approval` cho `external_send`/`destructive`; eval chạy sandbox dry-run;
ModelRouter áp dụng cả trong eval; publish cần xác nhận + version immutable + rollback; mọi lệnh,
diff, kết quả eval ghi audit.

## 11. Các bước (Phase 2)

| Bước | Nội dung |
|---|---|
| 2.1 | Validator + JSON Patch/diff + `builder_sessions` |
| 2.2 | Catalog API lọc theo quyền + enum động |
| 2.3 | Graph: classify → understand → clarify → map → compose → validate |
| 2.4 | Eval: sinh case, sandbox, judge, `eval_suite` |
| 2.5 | Luồng sửa: locate → patch → re-eval → diff |
| 2.6 | API + SSE + UI |
| 2.7 | Publish/rollback có xác nhận + audit |
| 2.8 | Golden dataset 40 mô tả trong CI |

## 12. Hiện trạng triển khai (2026-11-19)

- Module `backend/src/aiagent/modules/builder` — domain (Brief, Plan, Patch/diff, slash command),
  application (`ArchitectService`, `CommandService`), infrastructure (bộ não LLM + heuristic, repo SQL).
- **Hai bộ não** cùng `ArchitectBrainPort`:
  - `LlmArchitectBrain` — Claude structured outputs (`output_config.format`), schema chặt từng bước,
    skill/tool/model ràng buộc bằng `enum` sinh từ catalog.
  - `HeuristicArchitectBrain` — theo từ khóa, chỉ cho dev/demo/test khi chưa có key
    (`AIAGENT_ARCHITECT_BRAIN=auto|llm|heuristic`; prod cấm heuristic).
- Eval chạy bằng `RunAgentService.run_ephemeral`: không ghi lịch sử, tool không phải `read` chạy dry-run.
- Bảo vệ nhiều lớp: enum catalog → `_sanitize_plan` bỏ mọi ID ngoài catalog → `AgentService.validate`;
  patch cấm `/approval`, `/schema_version`, `/graph_type`, `/knowledge_bases` và mọi path ngoài `scope`.
- API: `POST /api/v1/builder/sessions`, `POST …/{id}/messages` (SSE), `GET …/{id}`,
  `POST …/{id}/confirm`, `POST /api/v1/commands`. UI: `/builder`, command bar (Ctrl+K), ô mô tả ở trang chủ.
- Golden dataset: `backend/tests/golden/architect_cases.json` (40 mô tả). Chạy (tốn phí Claude):

```bash
docker compose -f deploy/docker-compose.yml --profile tools run --rm -e AIAGENT_GOLDEN=1 backend-dev pytest -m golden -s
```

# Provider LLM: Claude, Gemini, Groq, OpenRouter, Ollama

ARIES gọi model qua `ChatModelPort`. Có hai adapter:

| Adapter | Provider | Ghi chú |
|---|---|---|
| `AnthropicChatModel` | `anthropic` | Claude API (ADR-0014): thinking, effort, prompt caching, fallback |
| `OpenAICompatChatModel` | `gemini`, `groq`, `openrouter`, `ollama` | Mọi API tương thích OpenAI Chat Completions; streaming, tool calling, JSON theo schema |

Lịch sử hội thoại luôn lưu theo định dạng content block của Messages API. Adapter OpenAI-compatible
dịch qua lại (`tool_use` ↔ `tool_calls`, `tool_result` ↔ `role: tool`), nên đổi model giữa các
provider không cần migrate dữ liệu. Block `thinking` của Claude không gửi cho provider khác.

## Bật provider

Mỗi provider bật khi có key (Ollama: khi có URL). Bật cùng lúc bao nhiêu cũng được.

| Provider | Biến môi trường (`deploy/.env`) | Lấy key | Model |
|---|---|---|---|
| Gemini | `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> (có free tier) | Tự lấy từ API |
| Groq | `GROQ_API_KEY` | <https://console.groq.com/keys> (có free tier) | Tự lấy từ API |
| OpenRouter | `OPENROUTER_API_KEY` | <https://openrouter.ai/keys> | Chỉ model `:free` (đổi bằng `AIAGENT_OPENROUTER_FREE_ONLY=false`) |
| Ollama | `OLLAMA_BASE_URL` | Không cần key | Model đã `ollama pull` |

Danh sách model mặc định được **lấy từ endpoint `/models` của provider** (cache 5 phút), vì model
free thay đổi thường xuyên. Muốn khóa cứng danh sách: đặt `GEMINI_MODELS`, `GROQ_MODELS`,
`OPENROUTER_MODELS`, `OLLAMA_MODELS_ALLOWED` là JSON list, ví dụ `GROQ_MODELS=["llama-3.3-70b-versatile"]`.

Sau khi sửa `.env`: `docker compose up -d api worker`. Trang sửa agent sẽ hiện model theo nhóm
`Gemini · …`, `Ollama (local) · …`.

## Ollama chạy local

### Trong Docker Compose (Linux, có/không GPU)

```bash
cd deploy
# 1. Khai báo model cần pull trong .env, ví dụ: OLLAMA_MODELS=qwen3:8b llama3.2:3b
# 2. Chạy Ollama và pull model
docker compose --profile ollama up -d ollama ollama-pull
docker compose logs -f ollama-pull          # chờ pull xong
# 3. Trỏ ARIES tới Ollama
echo 'OLLAMA_BASE_URL=http://ollama:11434' >> .env
docker compose up -d api worker
```

- Model lưu ở volume `ollamadata`. Thêm model sau: `docker compose exec ollama ollama pull gemma3:4b`.
- GPU NVIDIA: bỏ comment khối `deploy.resources` của service `ollama` (cần nvidia-container-toolkit).
- API/worker gọi Ollama trực tiếp qua mạng nội bộ `app` (có trong `NO_PROXY`), không qua Squid.

### Backend chạy trực tiếp trên máy (macOS: dùng GPU Apple qua Metal)

Docker trên macOS không dùng được GPU, nên trên Mac nên cài Ollama bản native:

```bash
brew install ollama && ollama serve        # hoặc cài app từ https://ollama.com
ollama pull qwen3:8b
# backend/.env
AIAGENT_OLLAMA_BASE_URL=http://localhost:11434
```

### Chọn model

Agent cần **tool calling** tốt. Gợi ý theo RAM/VRAM:

| Bộ nhớ | Model gợi ý |
|---|---|
| 8 GB | `qwen3:4b`, `llama3.2:3b` |
| 16 GB | `qwen3:8b`, `gemma3:12b` |
| ≥ 32 GB / GPU 24 GB | `qwen3:32b`, `gpt-oss:20b` |

Model 3–4B dùng được cho hội thoại đơn giản nhưng hay gọi tool sai. Tăng `OLLAMA_CONTEXT_LENGTH`
(mặc định 16384) nếu agent có prompt/lịch sử dài; mặc định của Ollama quá nhỏ cho agent.

Model có chế độ suy luận (qwen3, deepseek-r1…): phần `<think>…</think>` hoặc trường `reasoning`
được hiển thị như "đang suy nghĩ" và không lưu vào lịch sử.

## Phân loại dữ liệu (ADR-0012)

- `ollama` được tính là **model tự host**: agent gắn nhãn `restricted` được phép dùng.
- `gemini`, `groq`, `openrouter` là cloud — bị chặn với `restricted` giống Claude.
- **Lưu ý gói miễn phí**: điều khoản free tier của một số provider (ví dụ Gemini API free tier,
  nhiều model `:free` trên OpenRouter) cho phép provider dùng dữ liệu để cải thiện sản phẩm.
  Chỉ dùng chúng cho agent `public`/`internal`; dữ liệu `confidential` nên dùng gói trả phí có
  cam kết không huấn luyện, hoặc Ollama.

## Agent Architect bằng provider khác

Architect cần sinh JSON theo schema. Ví dụ dùng Gemini:

```bash
ARCHITECT_PROVIDER=gemini
ARCHITECT_MODEL=<model gemini mạnh nhất bạn có>
ARCHITECT_FAST_MODEL=<model flash/lite>
ARCHITECT_JUDGE_MODEL=<model gemini>
```

Adapter thử `response_format: json_schema` trước; provider/model không hỗ trợ thì lùi về
`json_object` kèm schema trong system prompt. Kết quả luôn được validate lại bằng Pydantic.
Model local nhỏ thường không đủ tốt cho Architect.

## Giới hạn

- Chi phí hiển thị = 0 cho provider ngoài Claude (bảng giá chỉ có Claude).
- `effort` chỉ gửi cho Gemini (`reasoning_effort`); Groq/OpenRouter/Ollama bỏ qua.
- Free tier có rate limit thấp; vượt giới hạn → lỗi `llm_rate_limited` (HTTP 429).
- Không có prompt caching / server-side fallback như Claude.

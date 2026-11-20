import { inject, Injectable } from '@angular/core';
import { OidcSecurityService } from 'angular-auth-oidc-client';
import { firstValueFrom } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { RunEventMessage, RunEventName } from './models';

export interface ChatRequest {
  agentId: string;
  message: string;
  threadId?: string | null;
  draft?: boolean;
}

export class ChatStreamError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

/** Tách luồng SSE (`event: x\ndata: {...}\n\n`) thành RunEvent. Tách riêng để test được. */
export function parseSseChunk(buffer: string): {
  events: RunEventMessage[];
  rest: string;
} {
  const events: RunEventMessage[] = [];
  const normalized = buffer.replace(/\r\n/g, '\n');
  const parts = normalized.split('\n\n');
  const rest = parts.pop() ?? '';
  for (const part of parts) {
    let name = '';
    const data: string[] = [];
    for (const line of part.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (!name) continue;
    try {
      events.push({
        event: name as RunEventName,
        data: JSON.parse(data.join('\n') || '{}'),
      });
    } catch {
      // bỏ qua khung hỏng
    }
  }
  return { events, rest };
}

/**
 * Gọi POST /agents/{id}/chat và đọc SSE bằng fetch (EventSource không gửi được POST + header).
 * Token chỉ gắn cho API của chính hệ thống.
 */
@Injectable({ providedIn: 'root' })
export class ChatStreamService {
  private readonly oidc = inject(OidcSecurityService);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  chat(
    req: ChatRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<RunEventMessage> {
    return this.stream(
      `/v1/agents/${encodeURIComponent(req.agentId)}/chat`,
      {
        message: req.message,
        thread_id: req.threadId ?? null,
        draft: req.draft ?? false,
      },
      signal,
    );
  }

  /** Tiếp tục run đã dừng chờ duyệt (sau khi mọi yêu cầu có quyết định). */
  resume(threadId: string, signal?: AbortSignal): AsyncGenerator<RunEventMessage> {
    return this.stream(
      `/v1/threads/${encodeURIComponent(threadId)}/resume`,
      {},
      signal,
    );
  }

  /** POST JSON tới `path` (tương đối apiBaseUrl) và đọc SSE trả về. */
  async *stream(
    path: string,
    body: unknown,
    signal?: AbortSignal,
  ): AsyncGenerator<RunEventMessage> {
    const token = await firstValueFrom(this.oidc.getAccessToken());
    const response = await fetch(`${this.base}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
      signal,
      credentials: 'omit',
    });

    if (!response.ok || !response.body) {
      let message = `Lỗi ${response.status}`;
      let code: string | undefined;
      try {
        const errorBody = (await response.json()) as {
          error?: { message?: string; code?: string };
        };
        message = errorBody.error?.message ?? message;
        code = errorBody.error?.code;
      } catch {
        // không phải JSON
      }
      throw new ChatStreamError(message, response.status, code);
    }

    const reader = response.body
      .pipeThrough(new TextDecoderStream())
      .getReader();
    let buffer = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const parsed = parseSseChunk(buffer);
        buffer = parsed.rest;
        for (const event of parsed.events) yield event;
      }
      const tail = parseSseChunk(buffer + '\n\n');
      for (const event of tail.events) yield event;
    } finally {
      reader.releaseLock();
    }
  }
}

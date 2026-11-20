import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { ChatStreamService } from './chat-stream.service';
import {
  BuilderSessionSummary,
  BuilderSessionView,
  CommandResult,
  RunEventMessage,
} from './models';

@Injectable({ providedIn: 'root' })
export class BuilderApi {
  private readonly http = inject(HttpClient);
  private readonly sse = inject(ChatStreamService);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  createSession(agentId?: string | null): Observable<BuilderSessionView> {
    return this.http.post<BuilderSessionView>(
      `${this.base}/v1/builder/sessions`,
      {
        agent_id: agentId ?? null,
      },
    );
  }

  sessions(): Observable<BuilderSessionSummary[]> {
    return this.http.get<BuilderSessionSummary[]>(
      `${this.base}/v1/builder/sessions`,
    );
  }

  session(id: string): Observable<BuilderSessionView> {
    return this.http.get<BuilderSessionView>(
      `${this.base}/v1/builder/sessions/${encodeURIComponent(id)}`,
    );
  }

  /** Gửi mô tả / yêu cầu sửa / câu trả lời; nhận tiến trình qua SSE. */
  send(
    id: string,
    payload: { message: string } | { answers: Record<string, string> },
    signal?: AbortSignal,
  ): AsyncGenerator<RunEventMessage> {
    return this.sse.stream(
      `/v1/builder/sessions/${encodeURIComponent(id)}/messages`,
      payload,
      signal,
    );
  }

  confirmPublish(
    id: string,
    overrideEval: boolean,
    note = '',
  ): Observable<{ agent_id: string; version: number; published: boolean }> {
    return this.http.post<{
      agent_id: string;
      version: number;
      published: boolean;
    }>(`${this.base}/v1/builder/sessions/${encodeURIComponent(id)}/confirm`, {
      action: 'publish',
      override_eval: overrideEval,
      note,
    });
  }

  command(text: string): Observable<CommandResult> {
    return this.http.post<CommandResult>(`${this.base}/v1/commands`, { text });
  }
}

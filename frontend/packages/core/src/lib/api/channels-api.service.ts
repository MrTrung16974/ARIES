import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { ApiKeyInfo, ShareLinkInfo, TelegramBotInfo } from './models';

/** Publish agent ra ngoài (ADR-0020): link chia sẻ / widget nhúng, API key. */
@Injectable({ providedIn: 'root' })
export class ChannelsApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(APP_CONFIG).apiBaseUrl}/v1`;

  apiKeys(agentId: string): Observable<ApiKeyInfo[]> {
    return this.http.get<ApiKeyInfo[]>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/api-keys`,
    );
  }

  createApiKey(
    agentId: string,
    name: string,
    rateLimit: number,
  ): Observable<ApiKeyInfo> {
    return this.http.post<ApiKeyInfo>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/api-keys`,
      {
        name,
        rate_limit_per_min: rateLimit,
      },
    );
  }

  revokeApiKey(id: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/api-keys/${encodeURIComponent(id)}`,
    );
  }

  shares(agentId: string): Observable<ShareLinkInfo[]> {
    return this.http.get<ShareLinkInfo[]>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/share-links`,
    );
  }

  createShare(
    agentId: string,
    body: {
      name: string;
      allowed_origins: string[];
      rate_limit_per_min: number;
      daily_message_limit: number;
    },
  ): Observable<ShareLinkInfo> {
    return this.http.post<ShareLinkInfo>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/share-links`,
      body,
    );
  }

  updateShare(
    id: string,
    body: Partial<ShareLinkInfo>,
  ): Observable<ShareLinkInfo> {
    return this.http.patch<ShareLinkInfo>(
      `${this.base}/share-links/${encodeURIComponent(id)}`,
      body,
    );
  }

  rotateShare(id: string): Observable<ShareLinkInfo> {
    return this.http.post<ShareLinkInfo>(
      `${this.base}/share-links/${encodeURIComponent(id)}/rotate`,
      {},
    );
  }

  deleteShare(id: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/share-links/${encodeURIComponent(id)}`,
    );
  }

  feedback(
    threadId: string,
    messageId: number,
    rating: 1 | -1,
  ): Observable<unknown> {
    return this.http.post(
      `${this.base}/threads/${encodeURIComponent(threadId)}/messages/${messageId}/feedback`,
      { rating },
    );
  }

  telegramBots(agentId: string): Observable<TelegramBotInfo[]> {
    return this.http.get<TelegramBotInfo[]>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/telegram-bots`,
    );
  }

  /** Token chỉ gửi lên một lần, server cất vào OpenBao và không bao giờ trả lại. */
  createTelegramBot(
    agentId: string,
    body: {
      name: string;
      token: string;
      mode: 'polling' | 'webhook';
      allowed_chat_ids: number[];
      rate_limit_per_min: number;
    },
  ): Observable<TelegramBotInfo> {
    return this.http.post<TelegramBotInfo>(
      `${this.base}/agents/${encodeURIComponent(agentId)}/telegram-bots`,
      body,
    );
  }

  updateTelegramBot(
    id: string,
    body: Partial<TelegramBotInfo>,
  ): Observable<TelegramBotInfo> {
    return this.http.patch<TelegramBotInfo>(
      `${this.base}/telegram-bots/${encodeURIComponent(id)}`,
      body,
    );
  }

  deleteTelegramBot(id: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/telegram-bots/${encodeURIComponent(id)}`,
    );
  }
}

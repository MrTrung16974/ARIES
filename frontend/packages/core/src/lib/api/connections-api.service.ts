import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  Approval,
  ApprovalStatus,
  ConnectionInfo,
  ConnectionScope,
  CreateMcpServer,
  McpServerInfo,
  OAuthProviderInfo,
  ConnectorInfo,
  CreateConnection,
  MasterInfo,
  PendingState,
  UpdateConnection,
} from './models';

/** Kết nối dữ liệu (ADR-0016). Secret chỉ gửi lên, không bao giờ đọc về. */
@Injectable({ providedIn: 'root' })
export class ConnectionsApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  connectors(): Observable<ConnectorInfo[]> {
    return this.http.get<ConnectorInfo[]>(`${this.base}/v1/connectors`);
  }

  list(): Observable<ConnectionInfo[]> {
    return this.http.get<ConnectionInfo[]>(`${this.base}/v1/connections`);
  }

  create(body: CreateConnection): Observable<ConnectionInfo> {
    return this.http.post<ConnectionInfo>(`${this.base}/v1/connections`, body);
  }

  update(id: string, body: UpdateConnection): Observable<ConnectionInfo> {
    return this.http.patch<ConnectionInfo>(
      `${this.base}/v1/connections/${encodeURIComponent(id)}`,
      body,
    );
  }

  test(id: string): Observable<ConnectionInfo> {
    return this.http.post<ConnectionInfo>(
      `${this.base}/v1/connections/${encodeURIComponent(id)}/test`,
      {},
    );
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/v1/connections/${encodeURIComponent(id)}`,
    );
  }
}

/** Duyệt thao tác rủi ro (HITL). */
@Injectable({ providedIn: 'root' })
export class ApprovalsApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  list(
    filter: { status?: ApprovalStatus; threadId?: string } = {},
  ): Observable<Approval[]> {
    let params = new HttpParams();
    if (filter.status) params = params.set('status', filter.status);
    if (filter.threadId) params = params.set('thread_id', filter.threadId);
    return this.http.get<Approval[]>(`${this.base}/v1/approvals`, { params });
  }

  /** Trạng thái chờ duyệt của hội thoại (gồm yêu cầu của agent con khi chat với Master). */
  pending(threadId: string): Observable<PendingState> {
    return this.http.get<PendingState>(
      `${this.base}/v1/threads/${encodeURIComponent(threadId)}/pending`,
    );
  }

  /** ARIES Master của tổ chức (tự tạo lần đầu). */
  master(): Observable<MasterInfo> {
    return this.http.get<MasterInfo>(`${this.base}/v1/master`);
  }

  decide(id: string, approve: boolean, note?: string): Observable<Approval> {
    return this.http.post<Approval>(
      `${this.base}/v1/approvals/${encodeURIComponent(id)}/decision`,
      { approve, note: note || null },
    );
  }
}

/** OAuth broker (ADR-0018): bắt đầu kết nối tài khoản qua nhà cung cấp OAuth. */
@Injectable({ providedIn: 'root' })
export class OAuthApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  providers(): Observable<OAuthProviderInfo[]> {
    return this.http.get<OAuthProviderInfo[]>(`${this.base}/v1/oauth/providers`);
  }

  start(
    provider: string,
    body: { connector_id: string; name: string; scope: ConnectionScope },
  ): Observable<{ authorize_url: string }> {
    return this.http.post<{ authorize_url: string }>(
      `${this.base}/v1/oauth/${encodeURIComponent(provider)}/start`,
      body,
    );
  }
}

/** MCP server do Admin đăng ký (ADR-0018). Secret chỉ gửi lên. */
@Injectable({ providedIn: 'root' })
export class McpApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  list(): Observable<McpServerInfo[]> {
    return this.http.get<McpServerInfo[]>(`${this.base}/v1/mcp/servers`);
  }

  commands(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/v1/mcp/commands`);
  }

  create(body: CreateMcpServer): Observable<McpServerInfo> {
    return this.http.post<McpServerInfo>(`${this.base}/v1/mcp/servers`, body);
  }

  update(
    id: string,
    body: {
      name?: string;
      enabled?: boolean;
      secrets?: Record<string, string>;
      risk_overrides?: Record<string, string>;
    },
  ): Observable<McpServerInfo> {
    return this.http.patch<McpServerInfo>(
      `${this.base}/v1/mcp/servers/${encodeURIComponent(id)}`,
      body,
    );
  }

  sync(id: string): Observable<McpServerInfo> {
    return this.http.post<McpServerInfo>(
      `${this.base}/v1/mcp/servers/${encodeURIComponent(id)}/sync`,
      {},
    );
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/v1/mcp/servers/${encodeURIComponent(id)}`);
  }
}

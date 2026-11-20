import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  Approval,
  ApprovalStatus,
  ConnectionInfo,
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

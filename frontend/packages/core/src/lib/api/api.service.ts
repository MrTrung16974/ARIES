import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { AuditEvent, ChainStatus, PluginInfo, SystemInfo } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  systemInfo(): Observable<SystemInfo> {
    return this.http.get<SystemInfo>(`${this.base}/v1/system/info`);
  }

  plugins(): Observable<PluginInfo[]> {
    return this.http.get<PluginInfo[]>(`${this.base}/v1/system/plugins`);
  }

  auditEvents(limit = 50, beforeSeq?: number): Observable<AuditEvent[]> {
    let params = new HttpParams().set('limit', limit);
    if (beforeSeq) params = params.set('before_seq', beforeSeq);
    return this.http.get<AuditEvent[]>(`${this.base}/v1/audit/events`, {
      params,
    });
  }

  verifyAudit(): Observable<ChainStatus> {
    return this.http.get<ChainStatus>(`${this.base}/v1/audit/verify`);
  }
}

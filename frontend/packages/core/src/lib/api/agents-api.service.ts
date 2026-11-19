import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  AgentDetail,
  AgentSpec,
  AgentSummary,
  AgentVersion,
  ModelInfo,
  SkillInfo,
  StoredMessage,
  ThreadInfo,
} from './models';

@Injectable({ providedIn: 'root' })
export class AgentsApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl;

  list(): Observable<AgentSummary[]> {
    return this.http.get<AgentSummary[]>(`${this.base}/v1/agents`);
  }

  get(id: string): Observable<AgentDetail> {
    return this.http.get<AgentDetail>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}`,
    );
  }

  create(spec: AgentSpec, note = ''): Observable<AgentDetail> {
    return this.http.post<AgentDetail>(`${this.base}/v1/agents`, {
      spec,
      note,
      via: 'form',
    });
  }

  update(
    id: string,
    spec: AgentSpec,
    expectedVersion: number,
    note = '',
  ): Observable<AgentVersion> {
    return this.http.put<AgentVersion>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}`,
      {
        spec,
        note,
        via: 'form',
        expected_version: expectedVersion,
      },
    );
  }

  versions(id: string): Observable<AgentVersion[]> {
    return this.http.get<AgentVersion[]>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}/versions`,
    );
  }

  publish(id: string, version: number): Observable<AgentSummary> {
    return this.http.post<AgentSummary>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}/publish`,
      {
        version,
      },
    );
  }

  unpublish(id: string): Observable<AgentSummary> {
    return this.http.post<AgentSummary>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}/unpublish`,
      {},
    );
  }

  archive(id: string): Observable<AgentSummary> {
    return this.http.delete<AgentSummary>(
      `${this.base}/v1/agents/${encodeURIComponent(id)}`,
    );
  }

  skills(): Observable<SkillInfo[]> {
    return this.http.get<SkillInfo[]>(`${this.base}/v1/catalog/skills`);
  }

  models(): Observable<ModelInfo[]> {
    return this.http.get<ModelInfo[]>(`${this.base}/v1/catalog/models`);
  }

  threads(agentId: string): Observable<ThreadInfo[]> {
    const params = new HttpParams().set('agent_id', agentId);
    return this.http.get<ThreadInfo[]>(`${this.base}/v1/threads`, { params });
  }

  messages(threadId: string): Observable<StoredMessage[]> {
    return this.http.get<StoredMessage[]>(
      `${this.base}/v1/threads/${encodeURIComponent(threadId)}/messages`,
    );
  }
}

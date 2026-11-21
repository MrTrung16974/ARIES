import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  OpsFeedback,
  OpsFilter,
  OpsThread,
  OpsTranscript,
  OpsUsage,
} from './models';

function toParams(
  f: OpsFilter,
  extra: Record<string, string | number> = {},
): HttpParams {
  let p = new HttpParams();
  for (const [k, v] of Object.entries({ ...f, ...extra })) {
    if (v !== null && v !== undefined && v !== '') p = p.set(k, String(v));
  }
  return p;
}

/** Vận hành (ADR-0020): nhật ký hội thoại mọi kênh, feedback, chi phí. */
@Injectable({ providedIn: 'root' })
export class OpsApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(APP_CONFIG).apiBaseUrl}/v1/ops`;

  threads(f: OpsFilter, limit = 50, before?: string): Observable<OpsThread[]> {
    const extra: Record<string, string | number> = { limit };
    if (before) extra['before'] = before;
    return this.http.get<OpsThread[]>(`${this.base}/threads`, {
      params: toParams(f, extra),
    });
  }

  transcript(threadId: string): Observable<OpsTranscript> {
    return this.http.get<OpsTranscript>(
      `${this.base}/threads/${encodeURIComponent(threadId)}`,
    );
  }

  feedback(f: OpsFilter, limit = 100): Observable<OpsFeedback[]> {
    return this.http.get<OpsFeedback[]>(`${this.base}/feedback`, {
      params: toParams(f, { limit }),
    });
  }

  usage(f: OpsFilter): Observable<OpsUsage> {
    const rest: OpsFilter = { ...f, rating: null };
    return this.http.get<OpsUsage>(`${this.base}/usage`, {
      params: toParams(rest),
    });
  }
}

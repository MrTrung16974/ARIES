import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { CreateSchedule, ScheduleInfo, ScheduleRunInfo } from './models';

/** Lịch chạy agent tự động: cron hoặc mail mới (ADR-0021). */
@Injectable({ providedIn: 'root' })
export class AutomationApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(APP_CONFIG).apiBaseUrl}/v1/schedules`;

  list(everyone = false): Observable<ScheduleInfo[]> {
    return this.http.get<ScheduleInfo[]>(this.base, {
      params: everyone ? { everyone: true } : {},
    });
  }

  create(body: CreateSchedule): Observable<ScheduleInfo> {
    return this.http.post<ScheduleInfo>(this.base, body);
  }

  update(
    id: string,
    body: Partial<CreateSchedule> & { enabled?: boolean },
  ): Observable<ScheduleInfo> {
    return this.http.patch<ScheduleInfo>(
      `${this.base}/${encodeURIComponent(id)}`,
      body,
    );
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${encodeURIComponent(id)}`);
  }

  runNow(id: string): Observable<unknown> {
    return this.http.post(`${this.base}/${encodeURIComponent(id)}/run`, {});
  }

  runs(id: string): Observable<ScheduleRunInfo[]> {
    return this.http.get<ScheduleRunInfo[]>(
      `${this.base}/${encodeURIComponent(id)}/runs`,
    );
  }
}

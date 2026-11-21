import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  ConfigureModelProviderPayload,
  ModelCheckResult,
  ModelProviderInfo,
  ModelProviderProbeResult,
  TestModelProviderPayload,
} from './models';

@Injectable({ providedIn: 'root' })
export class ModelProvidersApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(APP_CONFIG).apiBaseUrl.replace(/\/$/, '');

  list(): Observable<ModelProviderInfo[]> {
    return this.http.get<ModelProviderInfo[]>(
      `${this.base}/v1/model-providers`,
    );
  }

  configure(
    providerId: string,
    body: ConfigureModelProviderPayload,
  ): Observable<ModelProviderInfo> {
    return this.http.put<ModelProviderInfo>(
      `${this.base}/v1/model-providers/${providerId}`,
      body,
    );
  }

  test(
    providerId: string,
    body: TestModelProviderPayload = {},
  ): Observable<ModelProviderProbeResult> {
    return this.http.post<ModelProviderProbeResult>(
      `${this.base}/v1/model-providers/${providerId}/test`,
      body,
    );
  }

  /** Gọi thử từng model (yêu cầu rất ngắn) — biết model nào thực sự dùng được. */
  checkModels(
    providerId: string,
    body: TestModelProviderPayload & { models: string[] },
  ): Observable<ModelCheckResult[]> {
    return this.http.post<ModelCheckResult[]>(
      `${this.base}/v1/model-providers/${providerId}/check-models`,
      body,
    );
  }

  remove(providerId: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/v1/model-providers/${providerId}`,
    );
  }
}

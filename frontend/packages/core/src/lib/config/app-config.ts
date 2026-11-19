import { InjectionToken } from '@angular/core';

/** Cấu hình runtime đọc từ `/assets/config.json` — đổi môi trường không cần build lại. */
export interface AppConfig {
  apiBaseUrl: string;
  oidc: {
    authority: string;
    clientId: string;
    scope?: string;
  };
}

export const APP_CONFIG = new InjectionToken<AppConfig>('APP_CONFIG');

export async function loadAppConfig(
  url = '/assets/config.json',
): Promise<AppConfig> {
  const response = await fetch(url, {
    cache: 'no-store',
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new Error(`Không tải được cấu hình ứng dụng (${response.status})`);
  }
  const config = (await response.json()) as Partial<AppConfig>;
  if (!config.apiBaseUrl || !config.oidc?.authority || !config.oidc?.clientId) {
    throw new Error('Cấu hình ứng dụng thiếu apiBaseUrl hoặc oidc');
  }
  return {
    apiBaseUrl: config.apiBaseUrl.replace(/\/$/, ''),
    oidc: {
      ...config.oidc,
      scope: config.oidc.scope ?? 'openid profile email',
    },
  };
}

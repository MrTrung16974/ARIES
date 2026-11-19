import {
  EnvironmentProviders,
  inject,
  makeEnvironmentProviders,
  provideAppInitializer,
} from '@angular/core';
import {
  LogLevel,
  OidcSecurityService,
  provideAuth,
} from 'angular-auth-oidc-client';
import { firstValueFrom } from 'rxjs';
import { AppConfig } from '../config/app-config';

/**
 * Đăng nhập Keycloak bằng Authorization Code + PKCE (S256).
 * Access token chỉ được gắn vào request tới `apiBaseUrl` (secureRoutes) — không rò sang domain khác.
 */
export function provideAppAuth(config: AppConfig): EnvironmentProviders {
  const origin = window.location.origin;
  return makeEnvironmentProviders([
    provideAuth({
      config: {
        authority: config.oidc.authority,
        clientId: config.oidc.clientId,
        scope: config.oidc.scope,
        responseType: 'code',
        redirectUrl: `${origin}/`,
        postLogoutRedirectUri: `${origin}/`,
        silentRenew: true,
        useRefreshToken: true,
        renewTimeBeforeTokenExpiresInSeconds: 30,
        ignoreNonceAfterRefresh: true,
        secureRoutes: [config.apiBaseUrl],
        logLevel: LogLevel.Warn,
      },
    }),
    // Xử lý callback / khôi phục phiên TRƯỚC khi router chạy guard.
    provideAppInitializer(() =>
      firstValueFrom(inject(OidcSecurityService).checkAuth()),
    ),
  ]);
}

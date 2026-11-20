import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { provideRouter, TitleStrategy, withComponentInputBinding } from '@angular/router';
import { APP_CONFIG, AppConfig, provideAppAuth } from '@aiagent/core';
import { authInterceptor } from 'angular-auth-oidc-client';
import { providePrimeNG } from 'primeng/config';
import { appRoutes } from './app.routes';
import { AriesPreset } from './theme/aries-theme';
import { AriesTitleStrategy } from './theme/title-strategy';

export function buildAppConfig(config: AppConfig): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      provideZonelessChangeDetection(),
      { provide: APP_CONFIG, useValue: config },
      provideHttpClient(withInterceptors([authInterceptor()])),
      provideAppAuth(config),
      provideRouter(appRoutes, withComponentInputBinding()),
      { provide: TitleStrategy, useClass: AriesTitleStrategy },
      providePrimeNG({
        theme: { preset: AriesPreset, options: { darkModeSelector: '.app-dark' } },
        ripple: false,
      }),
    ],
  };
}

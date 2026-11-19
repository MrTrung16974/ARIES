import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { APP_CONFIG, AppConfig, provideAppAuth } from '@aiagent/core';
import Aura from '@primeuix/themes/aura';
import { authInterceptor } from 'angular-auth-oidc-client';
import { providePrimeNG } from 'primeng/config';
import { appRoutes } from './app.routes';

export function buildAppConfig(config: AppConfig): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      provideZonelessChangeDetection(),
      { provide: APP_CONFIG, useValue: config },
      provideHttpClient(withInterceptors([authInterceptor()])),
      provideAppAuth(config),
      provideRouter(appRoutes, withComponentInputBinding()),
      providePrimeNG({
        theme: { preset: Aura, options: { darkModeSelector: '.app-dark' } },
        ripple: false,
      }),
    ],
  };
}

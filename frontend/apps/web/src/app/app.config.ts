import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import {
  provideRouter,
  TitleStrategy,
  withComponentInputBinding,
} from '@angular/router';
import { APP_CONFIG, AppConfig, provideAppAuth } from '@aiagent/core';
import { provideAppIcons } from '@aiagent/ui';
import { authInterceptor } from 'angular-auth-oidc-client';
import { appRoutes } from './app.routes';
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
      provideAppIcons(),
    ],
  };
}

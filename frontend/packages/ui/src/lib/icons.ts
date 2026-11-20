import {
  EnvironmentProviders,
  inject,
  makeEnvironmentProviders,
  provideAppInitializer,
} from '@angular/core';
import { MatIconRegistry } from '@angular/material/icon';
import { DomSanitizer } from '@angular/platform-browser';

/** Icon Material Symbols (Apache-2.0) tự host trong `assets/icons` — xem tools/scripts/copy-icons.mjs. */
export const APP_ICONS = [
  'add',
  'arrow_back',
  'bolt',
  'check',
  'close',
  'dashboard',
  'deployed_code',
  'edit',
  'forum',
  'home',
  'lock',
  'logout',
  'refresh',
  'robot_2',
  'save',
  'send',
  'shield',
  'stop',
  'verified',
] as const;

export type AppIcon = (typeof APP_ICONS)[number];

export function provideAppIcons(
  basePath = 'assets/icons',
): EnvironmentProviders {
  return makeEnvironmentProviders([
    provideAppInitializer(() => {
      const registry = inject(MatIconRegistry);
      const sanitizer = inject(DomSanitizer);
      for (const name of APP_ICONS) {
        // URL tĩnh do chính ứng dụng cung cấp (không phải dữ liệu người dùng/LLM).
        registry.addSvgIcon(
          name,
          sanitizer.bypassSecurityTrustResourceUrl(`${basePath}/${name}.svg`),
        );
      }
    }),
  ]);
}

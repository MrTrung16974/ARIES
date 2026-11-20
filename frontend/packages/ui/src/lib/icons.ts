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
  'code',
  'dark_mode',
  'dashboard',
  'deployed_code',
  'dns',
  'edit',
  'forum',
  'home',
  'light_mode',
  'lock',
  'logout',
  'person',
  'refresh',
  'robot_2',
  'save',
  'search',
  'send',
  'shield',
  'stop',
  'tune',
  'verified',
  'cable',
  'cancel',
  'check_circle',
  'database',
  'delete',
  'gavel',
  'key',
  'play_arrow',
  'travel_explore',
  'warning',
  'hub',
  'sync',
  'login',
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

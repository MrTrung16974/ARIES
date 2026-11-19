import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Role } from '../api/models';
import { SessionService } from './session.service';

/**
 * Chỉ cho vào route khi người dùng có một trong các vai trò (Owner luôn được).
 * Đây là kiểm tra UX — backend vẫn kiểm quyền độc lập cho mọi API.
 */
export function roleGuard(...roles: Role[]): CanActivateFn {
  return async () => {
    const session = inject(SessionService);
    const router = inject(Router);
    try {
      await session.load();
    } catch {
      return router.parseUrl('/forbidden');
    }
    return session.hasAnyRole(...roles) ? true : router.parseUrl('/forbidden');
  };
}

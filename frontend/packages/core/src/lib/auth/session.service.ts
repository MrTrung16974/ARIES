import { HttpClient } from '@angular/common/http';
import { computed, inject, Injectable, signal } from '@angular/core';
import { OidcSecurityService } from 'angular-auth-oidc-client';
import { firstValueFrom } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { Me, Role } from '../api/models';

/** Thông tin người dùng hiện tại (lấy từ API `/v1/me`, không tin claim phía client). */
@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly http = inject(HttpClient);
  private readonly oidc = inject(OidcSecurityService);
  private readonly config = inject(APP_CONFIG);

  private readonly _me = signal<Me | null>(null);
  private loading: Promise<Me> | null = null;

  readonly me = this._me.asReadonly();
  readonly displayName = computed(
    () => this._me()?.username ?? this._me()?.email ?? '',
  );

  load(force = false): Promise<Me> {
    if (!this.loading || force) {
      this.loading = firstValueFrom(
        this.http.get<Me>(`${this.config.apiBaseUrl}/v1/me`),
      )
        .then((me) => {
          this._me.set(me);
          return me;
        })
        .catch((err: unknown) => {
          this.loading = null;
          throw err;
        });
    }
    return this.loading;
  }

  hasAnyRole(...roles: Role[]): boolean {
    const mine = this._me()?.roles ?? [];
    return mine.includes('owner') || roles.some((r) => mine.includes(r));
  }

  logout(): void {
    this._me.set(null);
    this.loading = null;
    this.oidc.logoff().subscribe();
  }
}

import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { OidcSecurityService } from 'angular-auth-oidc-client';
import { of } from 'rxjs';
import { Me } from '../api/models';
import { APP_CONFIG } from '../config/app-config';
import { SessionService } from './session.service';

const me = (roles: Me['roles']): Me => ({
  subject: 's',
  tenant_id: 'default',
  username: 'alice',
  email: null,
  roles,
  created_at: '2026-01-01T00:00:00Z',
  last_seen_at: '2026-01-01T00:00:00Z',
});

describe('SessionService', () => {
  let service: SessionService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: APP_CONFIG, useValue: { apiBaseUrl: '/api', oidc: {} } },
        { provide: OidcSecurityService, useValue: { logoff: () => of(null) } },
      ],
    });
    service = TestBed.inject(SessionService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('tải hồ sơ một lần và kiểm tra vai trò', async () => {
    const p1 = service.load();
    const p2 = service.load();
    http.expectOne('/api/v1/me').flush(me(['builder']));
    await Promise.all([p1, p2]);

    expect(service.displayName()).toBe('alice');
    expect(service.hasAnyRole('builder')).toBe(true);
    expect(service.hasAnyRole('admin', 'auditor')).toBe(false);
  });

  it('owner vượt qua mọi kiểm tra vai trò', async () => {
    const p = service.load();
    http.expectOne('/api/v1/me').flush(me(['owner']));
    await p;
    expect(service.hasAnyRole('auditor')).toBe(true);
  });
});

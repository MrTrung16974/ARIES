import { TestBed } from '@angular/core/testing';
import { MatIconTestingModule } from '@angular/material/icon/testing';
import { provideRouter } from '@angular/router';
import { APP_CONFIG, BuilderApi, BuilderSessionView } from '@aiagent/core';
import { OidcSecurityService } from 'angular-auth-oidc-client';
import { of } from 'rxjs';
import { Builder } from './builder';

const view: BuilderSessionView = {
  id: 's1',
  status: 'awaiting_user',
  agent_id: null,
  base_version: null,
  brief: null,
  questions: [
    {
      id: 'q2',
      question: 'Ai sẽ dùng agent này?',
      options: [{ label: 'Nội bộ', value: 'nội bộ' }],
    },
  ],
  eval: null,
  spec: null,
  summary: null,
  permissions: [],
  brain: 'heuristic',
  messages: [{ role: 'user', content: 'Tạo trợ lý báo giờ', created_at: '' }],
};

describe('Builder', () => {
  it('khôi phục phiên đang chờ trả lời và hiện câu hỏi làm rõ', async () => {
    await TestBed.configureTestingModule({
      imports: [Builder, MatIconTestingModule],
      providers: [
        provideRouter([]),
        { provide: APP_CONFIG, useValue: { apiBaseUrl: '/api', oidc: {} } },
        {
          provide: OidcSecurityService,
          useValue: { getAccessToken: () => of('t') },
        },
        { provide: BuilderApi, useValue: { session: () => of(view) } },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(Builder);
    fixture.componentRef.setInput('session', 's1');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Tạo trợ lý báo giờ');
    expect(el.textContent).toContain('Ai sẽ dùng agent này?');
    expect(el.querySelector('.options button')?.textContent?.trim()).toBe(
      'Nội bộ',
    );
  });
});

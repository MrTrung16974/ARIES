import { loadAppConfig } from './app-config';

function mockFetch(body: unknown, status = 200): void {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
  );
}

describe('loadAppConfig', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('chuẩn hóa apiBaseUrl và thêm scope mặc định', async () => {
    mockFetch({
      apiBaseUrl: '/api/',
      oidc: { authority: 'http://kc/realms/a', clientId: 'web' },
    });
    const config = await loadAppConfig();
    expect(config.apiBaseUrl).toBe('/api');
    expect(config.oidc.scope).toBe('openid profile email');
  });

  it('báo lỗi khi thiếu oidc', async () => {
    mockFetch({ apiBaseUrl: '/api' });
    await expect(loadAppConfig()).rejects.toThrow(/oidc/);
  });

  it('báo lỗi khi tải thất bại', async () => {
    mockFetch({}, 404);
    await expect(loadAppConfig()).rejects.toThrow(/404/);
  });
});

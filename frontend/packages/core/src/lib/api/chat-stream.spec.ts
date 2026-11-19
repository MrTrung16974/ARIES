import { parseSseChunk } from './chat-stream.service';

describe('parseSseChunk', () => {
  it('tách nhiều sự kiện và giữ phần chưa hoàn chỉnh', () => {
    const { events, rest } = parseSseChunk(
      'event: token\ndata: {"text":"Xin"}\n\nevent: token\ndata: {"text":" chào"}\n\nevent: do',
    );
    expect(events).toEqual([
      { event: 'token', data: { text: 'Xin' } },
      { event: 'token', data: { text: ' chào' } },
    ]);
    expect(rest).toBe('event: do');
  });

  it('chấp nhận CRLF và bỏ qua khung hỏng', () => {
    const { events } = parseSseChunk(
      'event: done\r\ndata: {"status":"completed"}\r\n\r\nevent: x\ndata: {bad\n\n',
    );
    expect(events).toEqual([{ event: 'done', data: { status: 'completed' } }]);
  });
});

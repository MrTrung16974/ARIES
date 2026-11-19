import { StoredMessage } from '@aiagent/core';
import { toChatItems } from './agent-chat';

const msg = (
  role: 'user' | 'assistant',
  content: StoredMessage['content'],
): StoredMessage => ({
  id: 1,
  role,
  content,
  created_at: '2026-11-19T00:00:00Z',
});

describe('toChatItems', () => {
  it('gộp tool_use/tool_result vào một lượt assistant và bỏ thinking', () => {
    const items = toChatItems([
      msg('user', [{ type: 'text', text: 'mấy giờ rồi?' }]),
      msg('assistant', [
        { type: 'thinking', thinking: '', signature: 'x' },
        { type: 'tool_use', id: 't1', name: 'clock__now', input: {} },
      ]),
      msg('user', [
        { type: 'tool_result', tool_use_id: 't1', content: 'Thứ Năm 08:30' },
      ]),
      msg('assistant', [{ type: 'text', text: 'Bây giờ là 08:30.' }]),
    ]);

    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ role: 'user', text: 'mấy giờ rồi?' });
    expect(items[1].text).toBe('Bây giờ là 08:30.');
    expect(items[1].tools).toEqual([
      { id: 't1', name: 'clock.now', status: 'ok', preview: 'Thứ Năm 08:30' },
    ]);
  });

  it('đánh dấu tool lỗi', () => {
    const items = toChatItems([
      msg('assistant', [{ type: 'tool_use', id: 't1', name: 'x', input: {} }]),
      msg('user', [
        {
          type: 'tool_result',
          tool_use_id: 't1',
          content: 'hỏng',
          is_error: true,
        },
      ]),
    ]);
    expect(items[0].tools[0].status).toBe('error');
  });
});

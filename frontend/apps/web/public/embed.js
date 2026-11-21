/*
 * ARIES — widget chat nhúng (ADR-0020).
 * <script src="https://<aries>/embed.js" data-aries-token="<token>" async></script>
 * Tùy chọn: data-aries-title="Hỏi đáp", data-aries-color="#e11d74", data-aries-position="left".
 * Widget chỉ là iframe tới trang /c/<token> của ARIES — trang nhúng không đọc được hội thoại.
 */
(function () {
  'use strict';
  const script = document.currentScript;
  if (!script || window.__ariesWidget) return;
  window.__ariesWidget = true;

  const token = script.getAttribute('data-aries-token');
  if (!token) return;
  const base = new URL(script.src).origin;
  const color = script.getAttribute('data-aries-color') || '#e11d74';
  const title = script.getAttribute('data-aries-title') || 'Hỏi trợ lý';
  const left = script.getAttribute('data-aries-position') === 'left';
  const side = left ? 'left' : 'right';

  const button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('aria-label', title);
  button.title = title;
  button.textContent = '💬';
  button.style.cssText =
    'position:fixed;bottom:20px;' + side + ':20px;width:56px;height:56px;border-radius:50%;' +
    'border:none;cursor:pointer;font-size:26px;color:#fff;background:' + color + ';' +
    'box-shadow:0 6px 20px rgba(0,0,0,.25);z-index:2147483000;';

  let frame = null;
  function open() {
    if (!frame) {
      frame = document.createElement('iframe');
      frame.title = title;
      frame.src =
        base + '/c/' + encodeURIComponent(token) + '?embed=1&origin=' +
        encodeURIComponent(window.location.origin);
      frame.setAttribute('allow', 'clipboard-write');
      frame.style.cssText =
        'position:fixed;bottom:88px;' + side + ':20px;width:min(380px,calc(100vw - 40px));' +
        'height:min(600px,calc(100vh - 120px));border:none;border-radius:16px;background:#fff;' +
        'box-shadow:0 12px 40px rgba(0,0,0,.25);z-index:2147483000;';
      document.body.appendChild(frame);
    } else {
      frame.style.display = frame.style.display === 'none' ? 'block' : 'none';
    }
    button.textContent = frame.style.display === 'none' ? '💬' : '✕';
  }
  button.addEventListener('click', open);
  (document.body || document.documentElement).appendChild(button);
})();

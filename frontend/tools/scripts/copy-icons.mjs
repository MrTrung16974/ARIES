// Chép các icon Material Symbols (Apache-2.0) đang dùng vào assets — tránh tải cả bộ font.
// Thêm icon: bổ sung tên vào ICONS rồi chạy `node tools/scripts/copy-icons.mjs`.
import { copyFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

const ICONS = [
  'add', 'arrow_back', 'bolt', 'check', 'close', 'dashboard', 'deployed_code', 'edit',
  'forum', 'home', 'lock', 'logout', 'refresh', 'robot_2', 'save', 'send', 'shield',
  'stop', 'verified',
];
const src = 'node_modules/@material-symbols/svg-400/outlined';
const out = 'apps/web/public/assets/icons';
mkdirSync(out, { recursive: true });
for (const name of ICONS) copyFileSync(join(src, `${name}.svg`), join(out, `${name}.svg`));
copyFileSync('node_modules/@material-symbols/svg-400/LICENSE', join(out, 'LICENSE-material-symbols.txt'));
console.log(`Đã chép ${ICONS.length} icon vào ${out}`);

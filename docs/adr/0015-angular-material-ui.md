# ADR-0015: Angular Material thay PrimeNG

- Trạng thái: Accepted
- Ngày: 2026-11-20

## Bối cảnh
PrimeNG (mọi major 19–22) và PrimeIcons chuyển sang "PrimeUI License": cần license key, bản miễn
phí chỉ cho tổ chức rất nhỏ, cấm gỡ cơ chế license. Ứng dụng hiện banner "Invalid PrimeUI License".

## Quyết định
- UI dùng **Angular Material 3** (MIT), theme M3 palette `rose` theo màu logo ARIES.
- Icon: **Material Symbols** (Apache-2.0) dạng SVG tự host trong `assets/icons`
  (`tools/scripts/copy-icons.mjs`), đăng ký qua `MatIconRegistry` — không tải font ngoài (hợp CSP).
- Thư viện `packages/ui` (`@aiagent/ui`, tag `type:ui`): `ui-tag`, `ui-notice`, `provideAppIcons()`.
- Component chỉ dùng design token `--app-*` (khai báo trong `styles.scss` từ biến `--mat-sys-*`),
  không phụ thuộc biến riêng của thư viện UI.

## Hệ quả
+ Không phụ thuộc giấy phép thương mại; bundle nhỏ hơn (~177 kB so với ~191 kB nén).
+ Đổi thư viện UI sau này chỉ đụng `packages/ui` và template, token giữ nguyên.
- Một số component (tag, notice) tự viết thay vì có sẵn.

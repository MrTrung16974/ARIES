import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';
import { MatIcon } from '@angular/material/icon';

export type NoticeSeverity = 'success' | 'info' | 'warn' | 'error';

const ICON: Record<NoticeSeverity, string> = {
  success: 'check',
  info: 'verified',
  warn: 'shield',
  error: 'close',
};

/** Thông báo trong trang (thay p-message). Nội dung truyền qua content projection. */
@Component({
  selector: 'ui-notice',
  imports: [MatIcon],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class]': '"ui-notice sev-" + severity() + (compact() ? " compact" : "")',
    '[attr.role]': 'severity() === "error" ? "alert" : "status"',
  },
  template: `<mat-icon [svgIcon]="icon()" aria-hidden="true" />
    <div><ng-content /></div>`,
  styleUrl: './notice.scss',
})
export class Notice {
  readonly severity = input<NoticeSeverity>('info');
  readonly compact = input(false);
  protected readonly icon = computed(() => ICON[this.severity()]);
}

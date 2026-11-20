import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type Severity =
  'primary' | 'success' | 'info' | 'warn' | 'danger' | 'secondary';

/** Nhãn nhỏ (thay p-tag). */
@Component({
  selector: 'ui-tag',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[class]': '"ui-tag sev-" + severity()', role: 'status' },
  template: `{{ value() }}`,
  styleUrl: './tag.scss',
})
export class Tag {
  readonly value = input.required<string | number>();
  readonly severity = input<Severity>('primary');
}

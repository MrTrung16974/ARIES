import { inject, Injectable } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterStateSnapshot, TitleStrategy } from '@angular/router';

export const PRODUCT_NAME = 'ARIES';

/** Tiêu đề tab: "<trang> · ARIES". */
@Injectable({ providedIn: 'root' })
export class AriesTitleStrategy extends TitleStrategy {
  private readonly title = inject(Title);

  override updateTitle(snapshot: RouterStateSnapshot): void {
    const page = this.buildTitle(snapshot);
    this.title.setTitle(page ? `${page} · ${PRODUCT_NAME}` : PRODUCT_NAME);
  }
}

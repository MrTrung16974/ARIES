import { Component } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-forbidden',
  imports: [RouterLink, MatButtonModule, MatIcon],
  template: `
    <div class="box">
      <div class="icon-ring">
        <mat-icon svgIcon="lock" class="big" aria-hidden="true" />
      </div>
      <h1>Không có quyền truy cập</h1>
      <p class="muted">
        Tài khoản của bạn không có vai trò cần thiết cho trang này. Hãy liên hệ
        quản trị viên để được cấp quyền phù hợp.
      </p>
      <a mat-flat-button class="btn-aries" routerLink="/">
        <mat-icon svgIcon="home" /> Về trang chủ
      </a>
    </div>
  `,
  styles: `
    .box {
      max-width: 460px;
      margin: 3rem auto;
      text-align: center;
      padding: 2.5rem 2rem;
      background: var(--app-surface);
      border: 1px solid var(--app-border);
      border-radius: var(--app-radius-xl);
      box-shadow: var(--app-shadow-md);
    }
    .icon-ring {
      width: 64px;
      height: 64px;
      margin: 0 auto 1.15rem;
      border-radius: 18px;
      background: var(--app-highlight);
      border: 1px solid rgba(255, 61, 139, 0.35);
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .big {
      width: 32px;
      height: 32px;
      color: var(--app-primary);
    }
    p {
      margin: 0 0 1.5rem;
      line-height: 1.55;
    }
  `,
})
export class Forbidden {}

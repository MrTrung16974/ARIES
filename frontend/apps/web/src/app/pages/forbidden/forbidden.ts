import { Component } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-forbidden',
  imports: [RouterLink, MatButtonModule, MatIcon],
  template: `
    <div class="box">
      <mat-icon svgIcon="lock" class="big" aria-hidden="true" />
      <h1>Không có quyền truy cập</h1>
      <p>
        Tài khoản của bạn không có vai trò cần thiết cho trang này. Hãy liên hệ
        quản trị viên.
      </p>
      <a mat-flat-button routerLink="/"
        ><mat-icon svgIcon="home" /> Về trang chủ</a
      >
    </div>
  `,
  styles: `
    .box {
      text-align: center;
      padding: 3rem 1rem;
    }
    .big {
      width: 48px;
      height: 48px;
      color: var(--app-muted);
    }
  `,
})
export class Forbidden {}

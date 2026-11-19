import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';

@Component({
  selector: 'app-forbidden',
  imports: [RouterLink, ButtonModule],
  template: `
    <div class="box">
      <i class="pi pi-lock" aria-hidden="true"></i>
      <h1>Không có quyền truy cập</h1>
      <p>
        Tài khoản của bạn không có vai trò cần thiết cho trang này. Hãy liên hệ
        quản trị viên.
      </p>
      <a routerLink="/"><p-button label="Về trang chủ" icon="pi pi-home" /></a>
    </div>
  `,
  styles: `
    .box {
      text-align: center;
      padding: 3rem 1rem;
    }
    .pi-lock {
      font-size: 3rem;
      color: var(--p-text-muted-color);
    }
  `,
})
export class Forbidden {}

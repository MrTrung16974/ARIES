import { Component, computed, inject, OnInit } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { SessionService } from '@aiagent/core';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';

interface NavItem {
  label: string;
  icon: string;
  link: string;
  visible: boolean;
}

@Component({
  selector: 'app-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    ButtonModule,
    TagModule,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell implements OnInit {
  protected readonly session = inject(SessionService);

  protected readonly nav = computed<NavItem[]>(() => {
    this.session.me(); // phụ thuộc để tính lại khi hồ sơ tải xong
    return [
      { label: 'Tổng quan', icon: 'pi pi-home', link: '/', visible: true },
      {
        label: 'Agent',
        icon: 'pi pi-sparkles',
        link: '/agents',
        visible: true,
      },
      {
        label: 'Plugin',
        icon: 'pi pi-box',
        link: '/plugins',
        visible: this.session.hasAnyRole('admin'),
      },
      {
        label: 'Audit',
        icon: 'pi pi-shield',
        link: '/audit',
        visible: this.session.hasAnyRole('auditor'),
      },
    ].filter((i) => i.visible);
  });

  ngOnInit(): void {
    this.session.load().catch(() => undefined);
  }

  protected logout(): void {
    this.session.logout();
  }
}

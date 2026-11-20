import { Component, computed, inject, OnInit } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { SessionService } from '@aiagent/core';
import { AppIcon, Tag } from '@aiagent/ui';
import { CommandBar } from './command-bar';

interface NavItem {
  label: string;
  icon: AppIcon;
  link: string;
  visible: boolean;
}

@Component({
  selector: 'app-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatButtonModule,
    MatIcon,
    Tag,
    CommandBar,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell implements OnInit {
  protected readonly session = inject(SessionService);

  protected readonly nav = computed<NavItem[]>(() => {
    this.session.me(); // phụ thuộc để tính lại khi hồ sơ tải xong
    return (
      [
        { label: 'Tổng quan', icon: 'home', link: '/', visible: true },
        { label: 'Agent', icon: 'robot_2', link: '/agents', visible: true },
        {
          label: 'Architect',
          icon: 'bolt',
          link: '/builder',
          visible: this.session.hasAnyRole('builder', 'admin'),
        },
        {
          label: 'Plugin',
          icon: 'deployed_code',
          link: '/plugins',
          visible: this.session.hasAnyRole('admin'),
        },
        {
          label: 'Audit',
          icon: 'shield',
          link: '/audit',
          visible: this.session.hasAnyRole('auditor'),
        },
      ] satisfies NavItem[]
    ).filter((i) => i.visible);
  });

  ngOnInit(): void {
    this.session.load().catch(() => undefined);
  }

  protected logout(): void {
    this.session.logout();
  }
}

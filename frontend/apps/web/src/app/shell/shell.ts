import { DOCUMENT } from '@angular/common';
import {
  Component,
  computed,
  HostListener,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIcon } from '@angular/material/icon';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { SessionService } from '@aiagent/core';
import { AppIcon } from '@aiagent/ui';
import { CommandBar } from './command-bar';

interface NavItem {
  label: string;
  icon: AppIcon;
  link: string;
  visible: boolean;
}

const THEME_STORAGE_KEY = 'aries_theme_mode';
const SIDEBAR_STORAGE_KEY = 'aries_sidebar_collapsed';
const NARROW_BREAKPOINT = 960;

@Component({
  selector: 'app-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatButtonModule,
    MatIcon,
    CommandBar,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell implements OnInit {
  protected readonly session = inject(SessionService);
  private readonly doc = inject(DOCUMENT);

  protected readonly darkMode = signal<boolean>(false);
  protected readonly isNarrow = signal<boolean>(false);
  protected readonly collapsed = signal<boolean>(false);

  protected readonly userInitial = computed(() => {
    const name = this.session.displayName()?.trim();
    return name ? name.charAt(0).toUpperCase() : 'A';
  });

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
        { label: 'Kết nối', icon: 'cable', link: '/connections', visible: true },
        { label: 'Duyệt', icon: 'gavel', link: '/approvals', visible: true },
        { label: 'Tri thức', icon: 'menu_book', link: '/knowledge', visible: true },
        {
          label: 'MCP',
          icon: 'hub',
          link: '/mcp',
          visible: this.session.hasAnyRole('admin'),
        },
        {
          label: 'Plugin',
          icon: 'deployed_code',
          link: '/plugins',
          visible: this.session.hasAnyRole('admin'),
        },
        {
          label: 'Vận hành',
          icon: 'dashboard',
          link: '/ops',
          visible: this.session.hasAnyRole('admin', 'auditor', 'builder'),
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
    this.initTheme();
    this.initSidebar();
    this.session.load().catch(() => undefined);
  }

  @HostListener('window:resize')
  protected onResize(): void {
    const narrow = this.checkNarrow();
    const prevNarrow = this.isNarrow();
    if (narrow !== prevNarrow) {
      this.isNarrow.set(narrow);
      if (narrow) {
        this.collapsed.set(true);
      } else {
        this.collapsed.set(this.readSavedCollapsed());
      }
    }
  }

  protected toggleSidebar(): void {
    const next = !this.collapsed();
    this.collapsed.set(next);
    if (!this.isNarrow()) {
      try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // ignore storage errors
      }
    }
  }

  protected onNavClick(): void {
    if (this.isNarrow() && !this.collapsed()) {
      this.collapsed.set(true);
    }
  }

  protected toggleTheme(): void {
    const next = !this.darkMode();
    this.applyTheme(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next ? 'dark' : 'light');
    } catch {
      // ignore storage errors
    }
  }

  protected logout(): void {
    this.session.logout();
  }

  private initSidebar(): void {
    const narrow = this.checkNarrow();
    this.isNarrow.set(narrow);
    this.collapsed.set(narrow ? true : this.readSavedCollapsed());
  }

  private checkNarrow(): boolean {
    const win = this.doc.defaultView;
    return !!win && win.innerWidth <= NARROW_BREAKPOINT;
  }

  private readSavedCollapsed(): boolean {
    try {
      return localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  }

  private initTheme(): void {
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY);
      this.applyTheme(saved === 'dark');
    } catch {
      this.applyTheme(false);
    }
  }

  private applyTheme(dark: boolean): void {
    this.darkMode.set(dark);
    this.doc.documentElement.classList.toggle('dark-theme', dark);
  }
}

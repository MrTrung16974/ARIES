import { Route } from '@angular/router';
import { roleGuard } from '@aiagent/core';
import { autoLoginPartialRoutesGuard } from 'angular-auth-oidc-client';
import { Shell } from './shell/shell';

export const appRoutes: Route[] = [
  {
    path: '',
    component: Shell,
    canActivate: [autoLoginPartialRoutesGuard],
    children: [
      {
        path: '',
        title: 'Tổng quan',
        loadComponent: () => import('./pages/home/home').then((m) => m.Home),
      },
      {
        path: 'builder',
        title: 'Agent Architect',
        canActivate: [roleGuard('builder', 'admin')],
        loadComponent: () =>
          import('./pages/builder/builder').then((m) => m.Builder),
      },
      {
        path: 'agents',
        title: 'Agent',
        loadComponent: () =>
          import('./pages/agents/agents-list').then((m) => m.AgentsList),
      },
      {
        path: 'agents/new',
        title: 'Tạo agent',
        canActivate: [roleGuard('builder', 'admin')],
        loadComponent: () =>
          import('./pages/agents/agent-editor').then((m) => m.AgentEditor),
      },
      {
        path: 'agents/:id/edit',
        title: 'Sửa agent',
        canActivate: [roleGuard('builder', 'admin')],
        loadComponent: () =>
          import('./pages/agents/agent-editor').then((m) => m.AgentEditor),
      },
      {
        path: 'agents/:id/chat',
        title: 'Chat',
        loadComponent: () =>
          import('./pages/agents/agent-chat').then((m) => m.AgentChat),
      },
      {
        path: 'connections',
        title: 'Kết nối',
        loadComponent: () =>
          import('./pages/connections/connections').then((m) => m.Connections),
      },
      {
        path: 'approvals',
        title: 'Duyệt thao tác',
        loadComponent: () =>
          import('./pages/approvals/approvals').then((m) => m.Approvals),
      },
      {
        path: 'mcp',
        title: 'MCP server',
        canActivate: [roleGuard('admin')],
        loadComponent: () => import('./pages/mcp/mcp').then((m) => m.Mcp),
      },
      {
        path: 'plugins',
        title: 'Plugin',
        canActivate: [roleGuard('admin')],
        loadComponent: () =>
          import('./pages/plugins/plugins').then((m) => m.Plugins),
      },
      {
        path: 'audit',
        title: 'Nhật ký audit',
        canActivate: [roleGuard('auditor')],
        loadComponent: () => import('./pages/audit/audit').then((m) => m.Audit),
      },
      {
        path: 'forbidden',
        title: 'Không có quyền',
        loadComponent: () =>
          import('./pages/forbidden/forbidden').then((m) => m.Forbidden),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];

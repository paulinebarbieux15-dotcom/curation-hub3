import { Component, OnInit, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ApiService } from './api.service';
import { CommandPalette } from './ui/command-palette';
import { ProfileSwitcher } from './ui/profile-switcher';
import { Toasts } from './ui/toast.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ProfileSwitcher, Toasts, CommandPalette],
  template: `
    <header class="topbar">
      <a class="brand" routerLink="/"><span class="logo">◆</span> Curation<span>Hub</span></a>
      <nav class="nav">
        <a routerLink="/" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }">Dashboard</a>
        <a routerLink="/registry" routerLinkActive="on">Registry</a>
        <a routerLink="/reports" routerLinkActive="on">Reports</a>
        <a routerLink="/settings" routerLinkActive="on">Settings</a>
      </nav>
      <span class="grow"></span>
      <button class="kbd-btn" title="Command palette" (click)="openPalette()"><kbd>Ctrl</kbd><kbd>K</kbd></button>
      <span class="live" [class.on]="api.connected()" [title]="api.connected() ? 'Live updates on' : 'Reconnecting…'">●</span>
      <app-profile-switcher />
    </header>
    <main class="container"><router-outlet /></main>
    <app-toasts />
    <app-command-palette />
  `,
})
export class App implements OnInit {
  api = inject(ApiService);

  ngOnInit() { this.api.bootstrap(); }

  openPalette() {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));
  }
}

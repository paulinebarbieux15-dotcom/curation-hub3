import { Component, HostListener, computed, inject, signal } from '@angular/core';
import { ApiService } from '../api.service';
import { ProfilesManager } from './profiles-manager';
import { avatarColor, initials } from './util';

/** Header dropdown: switch profile, open the profile manager. */
@Component({
  selector: 'app-profile-switcher',
  imports: [ProfilesManager],
  template: `
    <div class="switcher">
      <button class="switcher-btn" (click)="open.set(!open())" aria-haspopup="menu" [attr.aria-expanded]="open()">
        @if (api.me(); as me) {
          <span class="avatar" [style.background]="color(me.id)">{{ ini(me.name) }}</span>
          <span class="switcher-name">{{ me.name }}</span>
        } @else {
          <span class="avatar empty">?</span>
          <span class="switcher-name muted">Select profile</span>
        }
        <span class="caret">▾</span>
      </button>
      @if (open()) {
        <div class="menu" role="menu">
          <div class="menu-title">Switch profile</div>
          @for (u of active(); track u.id) {
            <button role="menuitem" class="menu-item" [class.current]="u.id === api.userId()" (click)="pick(u.id)">
              <span class="avatar sm" [style.background]="color(u.id)">{{ ini(u.name) }}</span>
              <span class="grow">{{ u.name }}</span>
              @if (!u.available) { <span class="tag">away</span> }
              <span class="muted small">{{ u.todo }}</span>
              @if (u.online) { <span class="dot online" title="Online"></span> }
            </button>
          } @empty {
            <div class="menu-item muted">No profile yet</div>
          }
          <div class="menu-sep"></div>
          <button role="menuitem" class="menu-item" (click)="manage.set(true); open.set(false)">⚙ Manage profiles…</button>
        </div>
      }
    </div>

    @if (manage()) {
      <div class="modal-backdrop" (click)="manage.set(false)">
        <div class="modal wide" role="dialog" aria-modal="true" aria-label="Manage profiles" (click)="$event.stopPropagation()">
          <div class="modal-head">
            <h2>Profiles</h2>
            <button class="icon-btn" aria-label="Close" (click)="manage.set(false)">×</button>
          </div>
          <p class="muted small">Deleting a profile keeps its history (soft delete). Its pending items go to the global queue or are redistributed.</p>
          <app-profiles-manager />
        </div>
      </div>
    }
  `,
})
export class ProfileSwitcher {
  api = inject(ApiService);
  open = signal(false);
  manage = signal(false);
  active = computed(() => this.api.users().filter((u) => !u.deleted_at));
  ini = initials;
  color = avatarColor;

  pick(id: number) {
    this.api.selectUser(id);
    this.open.set(false);
  }

  @HostListener('document:click', ['$event'])
  outside(e: MouseEvent) {
    if (!(e.target as HTMLElement).closest('app-profile-switcher .switcher')) this.open.set(false);
  }

  @HostListener('document:keydown.escape')
  esc() {
    this.open.set(false);
    this.manage.set(false);
  }
}

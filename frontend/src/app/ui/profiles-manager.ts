import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api.service';
import { User } from '../models';
import { ToastService } from './toast.service';
import { avatarColor, errorMessage, formatDate, initials } from './util';

/** Add / edit / soft-delete / restore profiles. Used in the header modal and in Settings. */
@Component({
  selector: 'app-profiles-manager',
  imports: [FormsModule],
  template: `
    <form class="row gap" (ngSubmit)="add()">
      <input name="n" [(ngModel)]="newName" placeholder="New profile name" required maxlength="80" />
      <input name="r" [(ngModel)]="newRedactor" placeholder="AntBox redactor (optional)" list="redactors" />
      <button class="btn primary" [disabled]="!newName.trim()">Add profile</button>
    </form>
    <datalist id="redactors">
      @for (r of redactors(); track r) { <option [value]="r"></option> }
    </datalist>

    <table class="table compact mt">
      <thead><tr><th>Profile</th><th>AntBox redactor</th><th>Auto-distribution</th><th>To do</th><th></th></tr></thead>
      <tbody>
        @for (u of active(); track u.id) {
          <tr>
            <td>
              <div class="row gap-s">
                <span class="avatar" [style.background]="color(u.id)">{{ ini(u.name) }}</span>
                <input class="inline-input" [ngModel]="u.name" (change)="rename(u, $any($event.target).value)" [attr.aria-label]="'Name of ' + u.name" />
                @if (u.online) { <span class="dot online" title="Online"></span> }
              </div>
            </td>
            <td><input class="inline-input" list="redactors" [ngModel]="u.antbox_redactor || ''" placeholder="—"
                       (change)="setRedactor(u, $any($event.target).value)" /></td>
            <td>
              <label class="switch" [title]="u.available ? 'Receives new links' : 'Away: skipped by round-robin'">
                <input type="checkbox" [checked]="u.available" (change)="toggle(u)" />
                <span>{{ u.available ? 'Active' : 'Away' }}</span>
              </label>
            </td>
            <td>{{ u.todo }}</td>
            <td class="right">
              @if (confirmId() === u.id) {
                <div class="confirm">
                  <span class="small">Delete {{ u.name }}? {{ u.todo }} pending item(s) will go to:</span>
                  <button class="btn small" (click)="remove(u, 'global')">Global queue</button>
                  <button class="btn small" (click)="remove(u, 'round_robin')">Other analysts</button>
                  <button class="btn small ghost" (click)="confirmId.set(null)">Cancel</button>
                </div>
              } @else {
                <button class="btn small danger ghost" (click)="confirmId.set(u.id)">Delete</button>
              }
            </td>
          </tr>
        } @empty {
          <tr><td colspan="5" class="muted">No profile yet. Add the analysts of the team above.</td></tr>
        }
      </tbody>
    </table>

    @if (deleted().length) {
      <details class="mt">
        <summary class="muted">Deleted profiles ({{ deleted().length }}) — history is kept</summary>
        <ul class="plain">
          @for (u of deleted(); track u.id) {
            <li class="row gap">
              <span>{{ u.name }}</span><span class="muted small">deleted {{ date(u.deleted_at) }}</span>
              <button class="btn small ghost" (click)="restore(u)">Restore</button>
            </li>
          }
        </ul>
      </details>
    }
  `,
})
export class ProfilesManager {
  api = inject(ApiService);
  toast = inject(ToastService);
  newName = '';
  newRedactor = '';
  confirmId = signal<number | null>(null);
  active = computed(() => this.api.users().filter((u) => !u.deleted_at));
  deleted = computed(() => this.api.users().filter((u) => u.deleted_at));
  redactors = computed(() => this.api.referential().find((f) => f.name === 'Attachment redactor')?.values.map((v) => v.display) ?? []);
  ini = initials;
  color = avatarColor;
  date = (d: string | null) => formatDate(d, false);

  add() {
    const name = this.newName.trim();
    if (!name) return;
    this.api.createUser(name, this.newRedactor.trim() || undefined).subscribe({
      next: (r) => {
        this.toast.success(`Profile “${name}” created`);
        if (!this.api.userId()) this.api.selectUser(r.id);
        this.newName = '';
        this.newRedactor = '';
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
  rename(u: User, name: string) {
    if (name.trim() && name.trim() !== u.name) this.api.updateUser(u.id, { name: name.trim() }).subscribe();
  }
  setRedactor(u: User, value: string) { this.api.updateUser(u.id, { antbox_redactor: value }).subscribe(); }
  toggle(u: User) { this.api.updateUser(u.id, { available: !u.available }).subscribe(); }
  remove(u: User, reassign: 'global' | 'round_robin') {
    this.api.deleteUser(u.id, reassign).subscribe({
      next: (r) => {
        this.confirmId.set(null);
        this.toast.success(`${u.name} deleted — ${r.moved} item(s) ${reassign === 'global' ? 'moved to the global queue' : 'redistributed'}`,
          { label: 'Undo', run: () => this.restore(u) });
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
  restore(u: User) {
    this.api.restoreUser(u.id).subscribe(() => this.toast.success(`${u.name} restored`));
  }
}

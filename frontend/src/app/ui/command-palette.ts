import { Component, ElementRef, HostListener, computed, inject, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';
import { Subject, debounceTime, switchMap, of, catchError } from 'rxjs';
import { ApiService } from '../api.service';
import { Link } from '../models';
import { domainOf } from './util';

interface Cmd { label: string; hint?: string; run: () => void; }

/** Ctrl+K: jump to a page, switch profile, open any link by title/URL. */
@Component({
  selector: 'app-command-palette',
  template: `
    @if (open()) {
      <div class="modal-backdrop" (click)="close()">
        <div class="palette" role="dialog" aria-label="Command palette" (click)="$event.stopPropagation()">
          <input #box type="search" placeholder="Type a command, a profile or search links…" [value]="q()"
                 (input)="onInput($any($event.target).value)" (keydown)="nav($event)" />
          <ul>
            @for (c of items(); track $index) {
              <li [class.on]="$index === idx()" (mouseenter)="idx.set($index)" (click)="exec(c)">
                <span>{{ c.label }}</span>@if (c.hint) { <span class="muted small">{{ c.hint }}</span> }
              </li>
            } @empty { <li class="muted">No match</li> }
          </ul>
        </div>
      </div>
    }
  `,
})
export class CommandPalette {
  api = inject(ApiService);
  router = inject(Router);
  open = signal(false);
  q = signal('');
  idx = signal(0);
  found = signal<Link[]>([]);
  box = viewChild<ElementRef<HTMLInputElement>>('box');
  private search$ = new Subject<string>();

  private commands = computed<Cmd[]>(() => [
    { label: 'Go to Dashboard', hint: 'page', run: () => this.router.navigate(['/']) },
    { label: 'Go to Registry', hint: 'page', run: () => this.router.navigate(['/registry']) },
    { label: 'Go to Reports', hint: 'page', run: () => this.router.navigate(['/reports']) },
    { label: 'Go to Settings', hint: 'page', run: () => this.router.navigate(['/settings']) },
    { label: 'Manage profiles', hint: 'settings', run: () => this.router.navigate(['/settings'], { fragment: 'profiles' }) },
    ...this.api.users().filter((u) => !u.deleted_at).map((u) => ({
      label: `Switch to ${u.name}`, hint: 'profile', run: () => this.api.selectUser(u.id),
    })),
  ]);

  items = computed<Cmd[]>(() => {
    const q = this.q().toLowerCase().trim();
    const cmds = this.commands().filter((c) => !q || c.label.toLowerCase().includes(q));
    const links = this.found().map((l) => ({
      label: l.title || l.url, hint: `#${l.id} · ${domainOf(l.url)} · ${l.status}`,
      run: () => this.router.navigate(['/'], { queryParams: { link: l.id } }),
    }));
    return [...cmds, ...links].slice(0, 12);
  });

  constructor() {
    this.search$.pipe(
      debounceTime(200),
      switchMap((q) => q.trim().length < 2 ? of({ items: [] as Link[], total: 0 }) :
        this.api.links({ assignee: 'all', state: 'all', doc_type: '', date_from: '', date_to: '', q, sort: 'created_at', order: 'desc' }, 8)
          .pipe(catchError(() => of({ items: [] as Link[], total: 0 })))),
    ).subscribe((r) => this.found.set(r.items));
  }

  onInput(v: string) {
    this.q.set(v);
    this.idx.set(0);
    this.search$.next(v);
  }

  nav(e: KeyboardEvent) {
    const n = this.items().length;
    if (e.key === 'ArrowDown') { e.preventDefault(); this.idx.set((this.idx() + 1) % Math.max(1, n)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.idx.set((this.idx() - 1 + n) % Math.max(1, n)); }
    else if (e.key === 'Enter') { e.preventDefault(); const c = this.items()[this.idx()]; if (c) this.exec(c); }
    else if (e.key === 'Escape') { e.preventDefault(); this.close(); }
  }

  exec(c: Cmd) {
    this.close();
    c.run();
  }

  close() {
    this.open.set(false);
    this.q.set('');
    this.found.set([]);
  }

  @HostListener('document:keydown', ['$event'])
  hotkey(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      this.open.set(!this.open());
      setTimeout(() => this.box()?.nativeElement.focus());
    }
  }
}

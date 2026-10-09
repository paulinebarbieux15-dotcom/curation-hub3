import { Component, Injectable, inject, signal } from '@angular/core';

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'success' | 'error' | 'warning';
  action?: { label: string; run: () => void };
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly toasts = signal<Toast[]>([]);
  private seq = 0;

  show(text: string, kind: Toast['kind'] = 'info', action?: Toast['action'], ms = 5000) {
    const t: Toast = { id: ++this.seq, text, kind, action };
    this.toasts.update((l) => [...l.slice(-4), t]);
    setTimeout(() => this.dismiss(t.id), action ? Math.max(ms, 8000) : ms);
  }
  success(text: string, action?: Toast['action']) { this.show(text, 'success', action); }
  error(text: string) { this.show(text, 'error', undefined, 8000); }
  warning(text: string) { this.show(text, 'warning', undefined, 7000); }
  dismiss(id: number) { this.toasts.update((l) => l.filter((t) => t.id !== id)); }
}

@Component({
  selector: 'app-toasts',
  template: `
    <div class="toasts" aria-live="polite">
      @for (t of svc.toasts(); track t.id) {
        <div class="toast" [class]="'toast ' + t.kind">
          <span>{{ t.text }}</span>
          @if (t.action) {
            <button class="link-btn" (click)="t.action.run(); svc.dismiss(t.id)">{{ t.action.label }}</button>
          }
          <button class="icon-btn" aria-label="Dismiss" (click)="svc.dismiss(t.id)">×</button>
        </div>
      }
    </div>
  `,
})
export class Toasts {
  svc = inject(ToastService);
}

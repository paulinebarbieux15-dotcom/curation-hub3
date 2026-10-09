import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, switchMap, of, catchError } from 'rxjs';
import { ApiService } from '../api.service';
import { PreviewItem } from '../models';
import { ToastService } from './toast.service';
import { errorMessage, looksLikeUrl } from './util';

/** Smart paste (free text -> URLs, live duplicate check) + manual input with instant validation. */
@Component({
  selector: 'app-ingest-box',
  imports: [FormsModule],
  template: `
    <section class="card ingest">
      <div class="ingest-head">
        <h2>Add links</h2>
        <div class="seg" role="radiogroup" aria-label="Distribution">
          <span class="muted small">Assign to</span>
          @for (m of modes; track m.value) {
            <button type="button" role="radio" [attr.aria-checked]="mode() === m.value" [class.on]="mode() === m.value"
                    [title]="m.hint" (click)="mode.set(m.value)">{{ m.label }}</button>
          }
        </div>
      </div>

      <div class="ingest-grid">
        <div>
          <textarea rows="4" [ngModel]="text()" (ngModelChange)="onText($event)" (paste)="pasted = true"
                    placeholder="Paste anything — an email, chat messages, a list… every URL is detected automatically. Tracking parameters (utm_…) are removed and duplicates are flagged."
                    aria-label="Smart paste"></textarea>
          @if (items().length) {
            <div class="preview">
              <div class="row gap-s small">
                <b>{{ items().length }} URL(s) found</b>
                <span class="pill ok">{{ count('new') }} new</span>
                @if (count('duplicate')) { <span class="pill dup">{{ count('duplicate') }} already in base</span> }
                @if (count('repeat')) { <span class="pill dup">{{ count('repeat') }} repeated</span> }
                @if (count('invalid')) { <span class="pill bad">{{ count('invalid') }} invalid</span> }
                @if (checking()) { <span class="spinner sm"></span> }
              </div>
              <ul class="preview-list">
                @for (p of items(); track $index) {
                  <li [class]="'pv ' + p.state" [title]="p.message || p.normalized || ''">
                    <span class="pv-dot"></span>
                    <span class="pv-url">{{ p.url }}</span>
                    @if (p.social) { <span class="tag">social</span> }
                    @if (p.state === 'duplicate' && p.existing) {
                      <span class="small muted">#{{ p.existing.id }} · {{ p.existing.status }}{{ p.existing.assignee ? ' · ' + p.existing.assignee : '' }}</span>
                    } @else if (p.state !== 'new') { <span class="small muted">{{ p.message }}</span> }
                  </li>
                }
              </ul>
            </div>
          }
          <div class="row gap-s mt-s">
            <button class="btn primary" [disabled]="!count('new') || busy()" (click)="submitPaste()">
              Add {{ count('new') || '' }} link{{ count('new') === 1 ? '' : 's' }}
            </button>
            @if (text()) { <button class="btn ghost" (click)="clear()">Clear</button> }
            <span class="grow"></span>
            <label class="btn ghost small file-btn">
              Import file (CSV/TXT)
              <input type="file" accept=".csv,.txt,.tsv" (change)="importFile($event)" hidden />
            </label>
          </div>
        </div>

        <form class="manual" (ngSubmit)="submitManual()">
          <label class="lbl">
            <span>Single URL</span>
            <div class="input-wrap" [class.valid]="manualState() === 'valid'" [class.invalid]="manualState() === 'invalid'">
              <input [ngModel]="manual()" (ngModelChange)="manual.set($event)" name="manual" placeholder="https://…"
                     autocomplete="off" aria-label="Single URL" />
              <span class="state-icon">{{ manualState() === 'valid' ? '✓' : manualState() === 'invalid' ? '!' : '' }}</span>
            </div>
          </label>
          <span class="small" [class.err]="manualState() === 'invalid'">{{ manualHint() }}</span>
          <button class="btn" [disabled]="manualState() !== 'valid' || busy()">Add</button>
        </form>
      </div>
    </section>
  `,
})
export class IngestBox {
  api = inject(ApiService);
  toast = inject(ToastService);
  added = output<void>();

  modes = [
    { value: 'auto', label: 'Auto', hint: 'Round-robin among active profiles (capped; overflow goes to the global queue)' },
    { value: 'me', label: 'Me', hint: 'Assign to myself' },
    { value: 'global', label: 'Global queue', hint: 'Unassigned: anyone can claim' },
  ];
  mode = signal('auto');
  text = signal('');
  items = signal<PreviewItem[]>([]);
  checking = signal(false);
  busy = signal(false);
  manual = signal('');
  pasted = false;
  private text$ = new Subject<string>();

  manualState = computed(() => {
    const v = this.manual().trim();
    if (!v) return 'empty';
    return looksLikeUrl(v) ? 'valid' : 'invalid';
  });
  manualHint = computed(() => {
    switch (this.manualState()) {
      case 'invalid': return 'Not a valid web address (example: https://site.com/article)';
      case 'valid': return 'Looks good — duplicates are checked when you add it';
      default: return 'Type or paste one address, then press Enter';
    }
  });

  constructor() {
    this.text$.pipe(
      debounceTime(250),
      switchMap((t) => {
        if (!t.trim()) return of({ items: [] as PreviewItem[] });
        this.checking.set(true);
        return this.api.preview(t).pipe(catchError(() => of({ items: [] as PreviewItem[] })));
      }),
    ).subscribe((r) => {
      this.checking.set(false);
      this.items.set(r.items);
    });
  }

  count(state: PreviewItem['state']) { return this.items().filter((i) => i.state === state).length; }

  onText(v: string) {
    this.text.set(v);
    this.text$.next(v);
  }

  clear() {
    this.text.set('');
    this.items.set([]);
  }

  private needsProfile(): boolean {
    if (this.mode() === 'me' && !this.api.userId()) {
      this.toast.error('Select your profile (top right) to assign links to yourself.');
      return true;
    }
    return false;
  }

  private report(r: { created: unknown[]; duplicates: unknown[]; invalid: unknown[] }) {
    const parts = [`${r.created.length} link(s) added`];
    if (r.duplicates.length) parts.push(`${r.duplicates.length} duplicate(s) skipped`);
    if (r.invalid.length) parts.push(`${r.invalid.length} invalid`);
    (r.created.length ? this.toast.success.bind(this.toast) : this.toast.warning.bind(this.toast))(parts.join(' · '));
    this.added.emit();
  }

  submitPaste() {
    if (this.needsProfile()) return;
    const urls = this.items().filter((i) => i.state === 'new').map((i) => i.url);
    this.busy.set(true);
    this.api.ingest(urls, this.mode(), 'paste').subscribe({
      next: (r) => { this.busy.set(false); this.report(r); this.clear(); },
      error: (e) => { this.busy.set(false); this.toast.error(errorMessage(e)); },
    });
  }

  submitManual() {
    if (this.manualState() !== 'valid' || this.needsProfile()) return;
    this.busy.set(true);
    this.api.ingest([this.manual().trim()], this.mode(), 'manual').subscribe({
      next: (r) => {
        this.busy.set(false);
        if (r.duplicates.length) {
          const d = r.duplicates[0];
          this.toast.warning(`Already in the database: #${d.id} (${d.status}${d.assignee ? ', ' + d.assignee : ''})`);
        } else {
          this.report(r);
          this.manual.set('');
        }
      },
      error: (e) => { this.busy.set(false); this.toast.error(errorMessage(e)); },
    });
  }

  importFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || this.needsProfile()) return;
    this.api.ingestFile(file, this.mode()).subscribe({
      next: (r) => this.report(r),
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
}

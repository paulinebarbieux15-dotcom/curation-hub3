import { Component, ElementRef, HostListener, OnDestroy, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { ApiService } from '../api.service';
import { AiDecision, Link, LinkInfo, STATUS_LABEL } from '../models';
import { FieldPicker } from './field-picker';
import { RichEditor } from './rich-editor';
import { ToastService } from './toast.service';
import { avatarColor, domainOf, errorMessage, formatDate, initials, relativeTime } from './util';

const INFO_FIELDS: { key: keyof LinkInfo; label: string; type: string }[] = [
  { key: 'author', label: 'Author', type: 'text' },
  { key: 'published_date', label: 'Published', type: 'date' },
  { key: 'source', label: 'Source', type: 'text' },
  { key: 'country', label: 'Country', type: 'text' },
];

/** Side panel: review, edit and validate one link. */
@Component({
  selector: 'app-link-panel',
  imports: [FormsModule, FieldPicker, RichEditor],
  template: `
    <aside class="panel" role="dialog" aria-label="Link details">
      @if (link(); as l) {
        <header class="panel-head">
          <div class="row gap-s wrap">
            <span class="status" [class]="'status s-' + l.status">{{ label(l.status) }}</span>
            @if (l.stale) { <span class="badge" [class]="'badge ' + l.stale">{{ ageLabel(l) }}</span> }
            <span class="muted small">#{{ l.id }} · added {{ rel(l.created_at) }} · {{ l.source }}</span>
            <span class="grow"></span>
            <button class="icon-btn" title="Previous (K)" (click)="navigate.emit(-1)">↑</button>
            <button class="icon-btn" title="Next (J)" (click)="navigate.emit(1)">↓</button>
            <button class="icon-btn" title="Close (Esc)" aria-label="Close" (click)="close()">×</button>
          </div>
          <input class="title-input" [(ngModel)]="title" (ngModelChange)="dirty.set(true)" [disabled]="readonly()"
                 placeholder="Title" aria-label="Title" />
          <div class="row gap-s wrap small">
            <a [href]="l.url" target="_blank" rel="noopener" class="url">{{ domain(l.url) }} ↗</a>
            @if (l.final_url && l.final_url !== l.normalized_url) { <span class="muted" [title]="l.final_url">→ redirected</span> }
            @if (l.pdf_file) { <a [href]="api.pdfUrl(l.id)" target="_blank" rel="noopener">Open captured PDF</a> }
            <button class="link-btn" (click)="toggleText()">{{ text() === null ? 'Show captured text' : 'Hide text' }}</button>
          </div>
          @if (lockedByOther()) {
            <div class="alert warning">🔒 {{ l.locked_by_name }} is editing this link. You can read it; editing is disabled.</div>
          }
          @if (l.near_duplicate_of_link; as nd) {
            <div class="alert warning">Very similar content to #{{ nd.id }} “{{ nd.title }}” ({{ nd.status }}). Check it is not a duplicate.</div>
          }
          @if (l.duplicate_of_link; as d) {
            <div class="alert">Duplicate of #{{ d.id }} “{{ d.title }}” (same page after redirect / canonical URL).</div>
          }
          @if (l.error) { <div class="alert error">{{ l.error }}</div> }
        </header>

        <div class="panel-body">
          @if (text() !== null) {
            <section class="card-s"><h4>Captured text</h4><pre class="captured">{{ text() }}</pre></section>
          }
          @if (l.status === 'queued' || l.status === 'processing') {
            <div class="processing"><span class="spinner"></span> Capturing the page and running the AI auto-fill…</div>
          }

          <section>
            <div class="section-head">
              <h3>Summary</h3>
              <span class="muted small">AI draft — edit freely</span>
            </div>
            <app-rich-editor [value]="summary" [disabled]="readonly()" (changed)="summary = $event; dirty.set(true)" />
          </section>

          <section>
            <div class="section-head">
              <h3>AntBox metadata</h3>
              <span class="muted small">AI values are kept only when certain (≥ {{ threshold }}% + evidence). Hover a chip for the evidence.</span>
            </div>
            @for (f of api.referential(); track f.name) {
              <app-field-picker [field]="f" [values]="metadata()[f.name] || []" [decisions]="decisions(f.name)"
                                [disabled]="readonly()" (changed)="setField(f.name, $event)" />
            } @empty {
              <p class="muted">No AntBox referential loaded — import it in Settings.</p>
            }
          </section>

          <section>
            <div class="section-head"><h3>Information</h3></div>
            <div class="grid-2">
              @for (f of infoFields; track f.key) {
                <label class="lbl">
                  <span>{{ f.label }}
                    @if (infoAi(f.key); as d) { <span class="conf" [title]="'Evidence: “' + d.evidence + '”'">AI {{ (d.confidence * 100).toFixed(0) }}%</span> }
                  </span>
                  <input [type]="f.type" [ngModel]="info[f.key] || ''" (ngModelChange)="setInfo(f.key, $event)" [disabled]="readonly()" />
                </label>
              }
              <label class="lbl span-2">
                <span>Companies <span class="muted small">(comma separated)</span></span>
                <input [ngModel]="companies" (ngModelChange)="companies = $event; dirty.set(true)" [disabled]="readonly()" />
              </label>
            </div>
          </section>

          <section>
            <div class="section-head">
              <h3>Themes</h3>
              <span class="muted small">Dashboard only — never exported</span>
            </div>
            <div class="chips">
              @for (t of api.themes(); track t) {
                <button type="button" class="chip toggle" [class.on]="themes().includes(t)" [disabled]="readonly()"
                        (click)="toggleTheme(t)" [title]="l.themes_ai.includes(t) ? 'Suggested by the AI' : ''">
                  {{ t }} @if (l.themes_ai.includes(t)) { <span class="conf">AI</span> }
                </button>
              }
            </div>
          </section>

          <section>
            <div class="section-head"><h3>Assignment</h3></div>
            <div class="row gap wrap">
              <span class="avatar sm" [style.background]="color(l.assignee_id)">{{ ini(l.assignee_name) }}</span>
              <select [ngModel]="l.assignee_id ?? ''" (ngModelChange)="reassign($event)" aria-label="Assignee">
                <option value="">Global queue (unassigned)</option>
                @for (u of activeUsers(); track u.id) { <option [ngValue]="u.id">{{ u.name }}</option> }
              </select>
              @if (api.userId() && l.assignee_id !== api.userId() && !closed()) {
                <button class="btn small" (click)="claim()">Claim (C)</button>
              }
            </div>
          </section>

          @if (l.registry?.length) {
            <section>
              <div class="section-head"><h3>Registry</h3></div>
              <ul class="plain small">
                @for (r of l.registry; track r.id) {
                  <li>Entry #{{ r.id }} — approved by {{ r.operator_name }}, {{ date(r.approved_at) }} ·
                    <a [href]="api.registryFileUrl(r.id, 'pdf')">PDF</a> · <a [href]="api.registryFileUrl(r.id, 'csv')">CSV</a></li>
                }
              </ul>
            </section>
          }

          <details class="history">
            <summary>History ({{ l.history?.length || 0 }})</summary>
            <ul class="plain small">
              @for (h of l.history; track $index) {
                <li><span class="muted">{{ date(h.at) }}</span> · <b>{{ h.action }}</b> {{ h.user ? 'by ' + h.user : '' }} <span class="muted">{{ h.detail }}</span></li>
              }
            </ul>
          </details>
        </div>

        <footer class="panel-foot">
          <div class="export-group" role="group" aria-label="Export">
            <span class="muted small">Export</span>
            <a class="btn small" [class.disabled]="!l.pdf_file" [href]="l.pdf_file ? api.exportUrl(l.id, 'pdf') : null">PDF</a>
            <a class="btn small" [href]="api.exportUrl(l.id, 'csv')">CSV</a>
            <a class="btn small" [href]="api.exportUrl(l.id, 'both')" title="PDF + CSV with the same name (zip)">Both</a>
          </div>
          <span class="grow"></span>
          @if (l.status === 'to_review') {
            @if (rejecting()) {
              <input class="reason" [(ngModel)]="reason" placeholder="Reason (optional)" (keydown.enter)="reject()" />
              <button class="btn danger" (click)="reject()">Confirm reject</button>
              <button class="btn ghost" (click)="rejecting.set(false)">Cancel</button>
            } @else {
              <button class="btn ghost danger" [disabled]="readonly()" (click)="rejecting.set(true)" title="X">Reject</button>
              <button class="btn" [disabled]="readonly() || !dirty() || saving()" (click)="save()" title="Ctrl+S">Save</button>
              <button class="btn primary" [disabled]="readonly() || saving()" (click)="approve()" title="Ctrl+Enter or A">
                Approve{{ dirty() ? ' & save' : '' }}
              </button>
            }
          } @else if (closed()) {
            <span class="muted small">{{ label(l.status) }}{{ l.approved_by_name ? ' by ' + l.approved_by_name : '' }}</span>
            @if (l.status !== 'duplicate' || l.pdf_file) { <button class="btn" (click)="reopen()">Reopen</button> }
          } @else if (l.status === 'error') {
            <button class="btn primary" (click)="retry()">Retry capture</button>
          }
        </footer>
      } @else {
        <div class="panel-body"><div class="processing"><span class="spinner"></span> Loading…</div></div>
      }
    </aside>
  `,
})
export class LinkPanel implements OnDestroy {
  api = inject(ApiService);
  toast = inject(ToastService);

  linkId = input.required<number>();
  closed$ = output<void>({ alias: 'closed' });
  navigate = output<number>();
  changed = output<void>();

  link = signal<Link | null>(null);
  dirty = signal(false);
  saving = signal(false);
  rejecting = signal(false);
  text = signal<string | null>(null);
  metadata = signal<Record<string, string[]>>({});
  themes = signal<string[]>([]);
  title = '';
  summary = '';
  info: LinkInfo = {};
  companies = '';
  reason = '';
  infoFields = INFO_FIELDS;
  threshold = 80;

  readonly activeUsers = computed(() => this.api.users().filter((u) => !u.deleted_at));
  readonly lockedByOther = computed(() => {
    const l = this.link();
    return !!l && l.locked && l.locked_by !== this.api.userId();
  });
  readonly closed = computed(() => ['approved', 'rejected', 'duplicate'].includes(this.link()?.status ?? ''));
  readonly readonly = computed(() => this.link()?.status !== 'to_review' || this.lockedByOther() || !this.api.userId());

  private lockTimer?: ReturnType<typeof setInterval>;
  private sub: Subscription;
  label = (s: Link['status']) => STATUS_LABEL[s];
  rel = relativeTime;
  date = (d: string | null) => formatDate(d);
  domain = domainOf;
  ini = initials;
  color = avatarColor;

  constructor() {
    this.api.getSettings().subscribe((s) => (this.threshold = Math.round(s.llm.confidence_threshold * 100)));
    effect(() => {
      const id = this.linkId();
      untracked(() => this.open(id));
    });
    this.sub = this.api.events$.subscribe((e) => {
      const l = this.link();
      if (l && e.id === l.id && (e.type === 'link' || e.type === 'lock') && !this.dirty()) this.load(l.id, false);
    });
  }

  private host = inject(ElementRef<HTMLElement>);

  private open(id: number) {
    this.host.nativeElement.querySelector('.panel-body')?.scrollTo({ top: 0 });
    const prev = this.link();
    if (prev && prev.id !== id) this.releaseLock(prev.id);
    this.link.set(null);
    this.text.set(null);
    this.rejecting.set(false);
    this.load(id, true);
  }

  private load(id: number, lock: boolean) {
    this.api.link(id).subscribe({
      next: (l) => {
        if (id !== this.linkId()) return;
        this.link.set(l);
        this.title = l.title || '';
        this.summary = l.summary_html || '';
        this.metadata.set({ ...l.metadata });
        this.info = { ...l.info };
        this.companies = (l.info.companies || []).join(', ');
        this.themes.set([...l.themes]);
        this.dirty.set(false);
        if (lock && l.status === 'to_review') this.takeLock(l.id);
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  private takeLock(id: number) {
    clearInterval(this.lockTimer);
    if (!this.api.userId()) return;
    const renew = () => this.api.lock(id).subscribe({ next: () => {}, error: () => {} });
    renew();
    this.lockTimer = setInterval(renew, 30000);
  }

  private releaseLock(id: number) {
    clearInterval(this.lockTimer);
    if (this.api.userId()) this.api.unlock(id).subscribe({ error: () => {} });
  }

  ngOnDestroy() {
    const l = this.link();
    if (l) this.releaseLock(l.id);
    this.sub.unsubscribe();
  }

  decisions(field: string): AiDecision[] { return this.link()?.ai_details?.[field] ?? []; }
  infoAi(key: keyof LinkInfo): AiDecision | undefined {
    const value = this.info[key];
    return this.decisions('info.' + String(key)).find((d) => d.kept && d.value === value);
  }

  setField(name: string, values: string[]) {
    this.metadata.update((m) => ({ ...m, [name]: values }));
    this.dirty.set(true);
  }
  setInfo(key: keyof LinkInfo, value: string) {
    this.info = { ...this.info, [key]: value || null };
    this.dirty.set(true);
  }
  toggleTheme(t: string) {
    this.themes.update((l) => (l.includes(t) ? l.filter((x) => x !== t) : [...l, t]));
    this.dirty.set(true);
  }

  toggleText() {
    if (this.text() !== null) return this.text.set(null);
    this.api.linkText(this.linkId()).subscribe((r) => this.text.set(r.text || '(no text captured)'));
  }

  ageLabel(l: Link) {
    const days = Math.floor((Date.now() - new Date(l.created_at).getTime()) / 86400000);
    return `${days} days old`;
  }

  private body() {
    return {
      title: this.title,
      summary_html: this.summary,
      metadata: this.metadata(),
      info: { ...this.info, companies: this.companies.split(',').map((c) => c.trim()).filter(Boolean) },
      themes: this.themes(),
    };
  }

  save(then?: () => void) {
    const l = this.link();
    if (!l) return;
    this.saving.set(true);
    this.api.saveLink(l.id, this.body()).subscribe({
      next: (r) => {
        this.saving.set(false);
        this.dirty.set(false);
        const rej = Object.entries(r.rejected_values || {});
        if (rej.length) this.toast.warning('Values not in the referential were dropped: ' + rej.map(([k, v]) => `${k}: ${v.join(', ')}`).join('; '));
        this.changed.emit();
        if (then) then();
        else {
          this.toast.success('Saved');
          this.load(l.id, false);
        }
      },
      error: (e) => {
        this.saving.set(false);
        this.toast.error(errorMessage(e));
      },
    });
  }

  approve() {
    const l = this.link();
    if (!l || this.readonly()) return;
    const missing = this.api.referential().filter((f) => f.required && !(this.metadata()[f.name] || []).length);
    if (missing.length) {
      this.toast.error('Required field(s) empty: ' + missing.map((f) => f.name).join(', '));
      return;
    }
    const doApprove = () => this.api.approve(l.id).subscribe({
      next: (r) => {
        this.toast.success(`Approved — registry entry #${r.registry_id}`, { label: 'Undo', run: () => this.api.reopen(l.id).subscribe(() => this.changed.emit()) });
        this.changed.emit();
        this.navigate.emit(1);
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
    if (this.dirty()) this.save(doApprove);
    else doApprove();
  }

  reject() {
    const l = this.link();
    if (!l) return;
    this.api.reject(l.id, this.reason).subscribe({
      next: () => {
        this.reason = '';
        this.toast.success('Rejected', { label: 'Undo', run: () => this.api.reopen(l.id).subscribe(() => this.changed.emit()) });
        this.changed.emit();
        this.navigate.emit(1);
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  reopen() {
    const l = this.link()!;
    this.api.reopen(l.id).subscribe({
      next: () => { this.load(l.id, true); this.changed.emit(); },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
  retry() {
    const l = this.link()!;
    this.api.retry(l.id).subscribe({ next: () => { this.load(l.id, false); this.changed.emit(); }, error: (e) => this.toast.error(errorMessage(e)) });
  }
  claim() {
    const l = this.link()!;
    this.api.claim(l.id).subscribe({ next: () => { this.toast.success('Assigned to you'); this.load(l.id, false); this.changed.emit(); }, error: (e) => this.toast.error(errorMessage(e)) });
  }
  reassign(value: number | '') {
    const l = this.link()!;
    this.api.assign(l.id, value === '' ? null : Number(value)).subscribe({
      next: () => { this.load(l.id, false); this.changed.emit(); },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  close() {
    if (this.dirty() && !confirm('Discard unsaved changes?')) return;
    this.closed$.emit();
  }

  @HostListener('document:keydown', ['$event'])
  keys(e: KeyboardEvent) {
    const typing = (e.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]');
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (this.dirty() && !this.readonly()) this.save(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); this.approve(); return; }
    if (e.key === 'Escape' && !document.querySelector('.palette')) { this.close(); return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'a' || e.key === 'A') { e.preventDefault(); this.approve(); }
    else if (e.key === 'x' || e.key === 'X') { if (!this.readonly()) { e.preventDefault(); this.rejecting.set(true); } }
    else if (e.key === 'c' || e.key === 'C') { if (!this.closed() && this.link()?.assignee_id !== this.api.userId()) { e.preventDefault(); this.claim(); } }
  }
}

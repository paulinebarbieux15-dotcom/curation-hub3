import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { SlicePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, Subscription, debounceTime } from 'rxjs';
import { ApiService } from '../api.service';
import { ConnectorInfo, RegistryEntry } from '../models';
import { ToastService } from '../ui/toast.service';
import { display, errorMessage, formatDate } from '../ui/util';

@Component({
  selector: 'app-registry',
  imports: [FormsModule, SlicePipe],
  template: `
    <div class="page-head">
      <div>
        <h1>Registry</h1>
        <p class="muted">Immutable record of every approved link. Entries can never be edited or deleted; files are permanent.</p>
      </div>
      <div class="row gap-s">
        @if (check(); as c) {
          <span class="integrity" [class.ok]="c.ok" [class.bad]="!c.ok" [title]="'Last hash ' + c.last_hash">
            {{ c.ok ? '✔ Integrity verified' : '✖ ' + c.problems.length + ' problem(s)' }} · {{ c.entries }} entries
          </span>
        }
        <button class="btn" (click)="verify()">Verify chain</button>
      </div>
    </div>

    <section class="card connector">
      <div class="row gap wrap">
        <div class="grow">
          <h2>AntBox connector file</h2>
          <p class="muted small">One single CSV: every approved article adds one row. Import it into AntBox with the PDFs,
            then archive it to start a new file.</p>
          @if (conn(); as c) {
            <p><b>{{ c.rows }}</b> row{{ c.rows === 1 ? '' : 's' }} in <code>{{ c.file }}</code>
              @if (c.updated_at) { <span class="muted small"> · updated {{ date(c.updated_at) }}</span> }</p>
          }
        </div>
        <div class="row gap-s wrap">
          <a class="btn" [href]="api.connectorCsvUrl()">Download CSV</a>
          <a class="btn primary" [href]="api.connectorPackageUrl()">Download CSV + PDFs</a>
          <button class="btn ghost danger" [disabled]="!conn()?.rows" (click)="archive()">Imported? Archive & start new file</button>
        </div>
      </div>
      @if (conn()?.archives?.length) {
        <details class="mt-s"><summary class="muted small">Archived files ({{ conn()!.archives.length }})</summary>
          <ul class="plain small">
            @for (a of conn()!.archives; track a) { <li><a [href]="api.connectorArchiveUrl(a)">{{ a }}</a></li> }
          </ul>
        </details>
      }
    </section>
    @if (check()?.problems?.length) {
      <div class="alert error">
        @for (p of check()!.problems; track $index) { <div>Entry #{{ p.id }}: {{ p.problem }}</div> }
      </div>
    }

    <section class="card table-card">
      <div class="filters">
        <input class="search" type="search" placeholder="Search title, URL, metadata…" [ngModel]="q" (ngModelChange)="q = $event; search$.next()" />
        <label class="date-range">
          <input type="date" [(ngModel)]="from" (ngModelChange)="load()" aria-label="From" /> <span>→</span>
          <input type="date" [(ngModel)]="to" (ngModelChange)="load()" aria-label="To" />
        </label>
        <select [(ngModel)]="operator" (ngModelChange)="load()" aria-label="Operator">
          <option value="">All operators</option>
          @for (o of operators(); track o) { <option [value]="o">{{ o }}</option> }
        </select>
        <span class="grow"></span>
        <span class="muted small">{{ entries().length }} entr{{ entries().length === 1 ? 'y' : 'ies' }}</span>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>#</th><th>Date</th><th>Title</th><th>Metadata</th><th>Validated by</th><th>Files</th></tr></thead>
          <tbody>
            @for (e of entries(); track e.id) {
              <tr>
                <td class="muted">{{ e.id }}</td>
                <td class="nowrap">{{ date(e.approved_at) }}</td>
                <td class="title-cell">
                  <div class="t-title"><a [href]="e.url" target="_blank" rel="noopener">{{ e.title || e.url }}</a></div>
                  <div class="t-sub" [title]="e.summary_text">{{ e.summary_text | slice: 0 : 140 }}{{ (e.summary_text || '').length > 140 ? '…' : '' }}</div>
                </td>
                <td>
                  <div class="meta-list">
                    @for (kv of meta(e); track kv[0]) {
                      <div><span class="muted small">{{ kv[0] }}:</span> {{ kv[1] }}</div>
                    }
                  </div>
                </td>
                <td class="nowrap">{{ e.operator_name }}</td>
                <td class="nowrap">
                  @if (e.pdf_file) { <a class="btn small" [href]="api.registryFileUrl(e.id, 'pdf')" [title]="'SHA-256 ' + e.pdf_sha256">PDF</a> }
                  <a class="btn small" [href]="api.registryFileUrl(e.id, 'csv')" [title]="'SHA-256 ' + e.csv_sha256">CSV</a>
                </td>
              </tr>
            } @empty {
              <tr><td colspan="6" class="empty">No approved link yet.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class Registry implements OnInit, OnDestroy {
  api = inject(ApiService);
  toast = inject(ToastService);
  entries = signal<RegistryEntry[]>([]);
  conn = signal<ConnectorInfo | null>(null);
  check = signal<{ ok: boolean; entries: number; problems: { id: number; problem: string }[]; last_hash: string } | null>(null);
  q = '';
  from = '';
  to = '';
  operator = '';
  search$ = new Subject<void>();
  private subs: Subscription[] = [];
  operators = computed(() => [...new Set(this.api.users().map((u) => u.name))].sort());
  date = (d: string) => formatDate(d);

  ngOnInit() {
    this.subs.push(
      this.search$.pipe(debounceTime(250)).subscribe(() => this.load()),
      this.api.events$.subscribe((e) => { if (e.type === 'registry') this.load(); }),
    );
    this.load();
    this.verify();
  }
  ngOnDestroy() { this.subs.forEach((s) => s.unsubscribe()); }

  loadConnector() { this.api.connector().subscribe((c) => this.conn.set(c)); }

  archive() {
    const c = this.conn();
    if (!c || !confirm(`Archive the connector file (${c.rows} rows) and start a new empty one? Do this only after importing it into AntBox.`)) return;
    this.api.archiveConnector().subscribe({
      next: (r) => { this.toast.success(`Archived as ${r.archived} — new empty file started`); this.loadConnector(); },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  load() {
    this.loadConnector();
    this.api.registry(this.q, this.from, this.to, this.operator).subscribe({
      next: (r) => this.entries.set(r),
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
  verify() { this.api.verifyRegistry().subscribe((c) => this.check.set(c)); }

  meta(e: RegistryEntry): [string, string][] {
    const out: [string, string][] = [];
    for (const [k, v] of Object.entries(e.metadata?.antbox || {})) {
      if (v?.length) out.push([k, v.map(display).join(', ')]);
    }
    return out.slice(0, 4);
  }
}

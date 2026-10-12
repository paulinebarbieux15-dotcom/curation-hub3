import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api.service';
import { Report } from '../models';
import { ToastService } from '../ui/toast.service';
import { errorMessage, formatDate } from '../ui/util';

@Component({
  selector: 'app-reports',
  imports: [FormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Reports</h1>
        <p class="muted">Executive synthesis written by the AI from validated links, with every link and its summary appended.</p>
      </div>
    </div>

    <div class="reports-grid">
      <section class="card">
        <h2>New report</h2>
        <div class="lbl"><span>Period</span>
          <div class="seg">
            @for (p of periods; track p.v) { <button [class.on]="period === p.v" (click)="period = p.v">{{ p.l }}</button> }
          </div>
        </div>
        @if (period === 'custom') {
          <div class="row gap-s">
            <label class="lbl grow"><span>From</span><input type="date" [(ngModel)]="start" /></label>
            <label class="lbl grow"><span>To</span><input type="date" [(ngModel)]="end" /></label>
          </div>
        }
        <div class="lbl"><span>Granularity</span>
          <div class="seg">
            @for (g of grans; track g.v) { <button [class.on]="granularity === g.v" (click)="granularity = g.v" [title]="g.h">{{ g.l }}</button> }
          </div>
        </div>
        <label class="lbl"><span>Title <span class="muted small">(optional)</span></span>
          <input [(ngModel)]="title" placeholder="Technology watch report — …" /></label>
        <button class="btn primary full" [disabled]="busy() || (period === 'custom' && (!start || !end))" (click)="generate()">
          @if (busy()) { <span class="spinner sm"></span> Writing the synthesis… } @else { Generate report }
        </button>

        <h3 class="mt">History</h3>
        <ul class="report-list">
          @for (r of list(); track r.id) {
            <li [class.on]="current()?.id === r.id" (click)="open(r.id)">
              <div class="t-title">{{ r.title }}</div>
              <div class="t-sub">{{ r.link_count }} links · {{ r.granularity }} · {{ date(r.created_at) }}{{ r.created_by_name ? ' · ' + r.created_by_name : '' }}</div>
            </li>
          } @empty { <li class="muted">No report yet.</li> }
        </ul>
      </section>

      <section class="card report-view">
        @if (current(); as r) {
          <div class="row gap-s report-actions">
            <span class="muted small">{{ r.period_start }} → {{ r.period_end }} · {{ r.link_count }} links</span>
            <span class="grow"></span>
            @if (r.pdf_file) { <a class="btn small primary" [href]="api.reportFileUrl(r.id, 'pdf')">Download PDF</a> }
            <a class="btn small" [href]="api.reportFileUrl(r.id, 'html')" target="_blank" rel="noopener">HTML</a>
            <a class="btn small" [href]="api.reportFileUrl(r.id, 'md')">Markdown</a>
            <button class="btn small" (click)="copy(r)">Copy text</button>
          </div>
          <article class="report-body" [innerHTML]="r.html"></article>
        } @else {
          <div class="empty">Generate a report or pick one from the history.</div>
        }
      </section>
    </div>
  `,
})
export class Reports implements OnInit {
  api = inject(ApiService);
  toast = inject(ToastService);
  periods = [{ v: '7d', l: 'Last 7 days' }, { v: '30d', l: 'Last month' }, { v: 'custom', l: 'Custom' }];
  grans = [
    { v: 'general', l: 'General', h: 'One list' },
    { v: 'doctype', l: 'By document type', h: 'Grouped by document type' },
    { v: 'user', l: 'By user', h: 'Grouped by analyst' },
  ];
  period = '7d';
  granularity = 'general';
  start = '';
  end = '';
  title = '';
  busy = signal(false);
  list = signal<Report[]>([]);
  current = signal<Report | null>(null);
  date = (d: string) => formatDate(d);

  ngOnInit() { this.loadList(); }

  loadList() { this.api.reports().subscribe((l) => this.list.set(l)); }

  open(id: number) { this.api.report(id).subscribe((r) => this.current.set(r)); }

  generate() {
    this.busy.set(true);
    this.api.createReport({
      period: this.period, granularity: this.granularity, title: this.title || undefined,
      start: this.period === 'custom' ? this.start : undefined, end: this.period === 'custom' ? this.end : undefined,
    }).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.current.set(r);
        this.loadList();
        this.toast.success(`Report ready — ${r.link_count} link(s)`);
      },
      error: (e) => { this.busy.set(false); this.toast.error(errorMessage(e)); },
    });
  }

  copy(r: Report) {
    navigator.clipboard?.writeText(r.markdown || '').then(() => this.toast.success('Markdown copied'));
  }
}

import { Component, HostListener, OnDestroy, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subject, Subscription, debounceTime } from 'rxjs';
import { ApiService } from '../api.service';
import { Link, LinkFilters, STATUS_LABEL, Stats } from '../models';
import { IngestBox } from '../ui/ingest-box';
import { LinkPanel } from '../ui/link-panel';
import { Sparkline } from '../ui/sparkline';
import { ToastService } from '../ui/toast.service';
import { avatarColor, domainOf, download, errorMessage, initials, relativeTime } from '../ui/util';

const FILTER_KEY = 'curation.filters';
const DEFAULT_FILTERS: LinkFilters = {
  assignee: 'me', state: 'todo', theme: '', date_from: '', date_to: '', q: '', sort: 'created_at', order: 'desc',
};

function storedFilters(): LinkFilters {
  try {
    return { ...DEFAULT_FILTERS, ...JSON.parse(localStorage.getItem(FILTER_KEY) || '{}') };
  } catch {
    return { ...DEFAULT_FILTERS };
  }
}

@Component({
  selector: 'app-dashboard',
  imports: [FormsModule, IngestBox, LinkPanel, Sparkline],
  template: `
    <!-- ============ widgets ============ -->
    <section class="widgets">
      <div class="widget">
        <div class="w-label">My queue</div>
        <div class="w-value">{{ s()?.my_todo ?? '–' }}</div>
        <div class="w-sub">{{ s()?.my_to_review ?? 0 }} ready to review</div>
        <button class="w-link" (click)="quick('me')">Show →</button>
      </div>
      <div class="widget">
        <div class="w-label">Global queue</div>
        <div class="w-value">{{ s()?.global_queue ?? '–' }}</div>
        <div class="w-sub">unassigned · team total {{ s()?.team_todo ?? 0 }}</div>
        <button class="w-link" (click)="quick('global')">Show →</button>
      </div>
      <div class="widget">
        <div class="w-label">Progress today</div>
        <div class="w-value">{{ s()?.done_today_me ?? 0 }}<span class="w-unit"> done</span></div>
        <div class="progress" role="progressbar" [attr.aria-valuenow]="s()?.progress_today_me ?? 0" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar" [style.width.%]="s()?.progress_today_me ?? 0"></div>
        </div>
        <div class="w-sub">{{ s()?.progress_today_me ?? 0 }}% of my day · team {{ s()?.done_today_team ?? 0 }} today</div>
      </div>
      <div class="widget">
        <div class="w-label">Velocity (7 days)</div>
        <div class="row gap">
          <div>
            <div class="w-value">{{ s()?.velocity?.avg_team_per_day ?? 0 }}</div>
            <div class="w-sub">team / day</div>
          </div>
          @if (s(); as st) { <app-sparkline [team]="st.velocity.team" [me]="st.velocity.me" [days]="st.velocity.days" /> }
        </div>
        <div class="w-sub legend"><i class="lg-me"></i> me <i class="lg-team"></i> team</div>
      </div>
      <div class="widget">
        <div class="w-label">Health</div>
        <div class="health">
          <span title="Being captured / analysed">⏳ {{ s()?.processing ?? 0 }}</span>
          <span [class.warn]="(s()?.errors ?? 0) > 0" title="Capture errors">⚠ {{ s()?.errors ?? 0 }}</span>
          <span [class.warn]="(s()?.stale?.warning ?? 0) > 0" [title]="'Older than ' + (s()?.stale?.warning_days ?? 3) + ' days'">🕒 {{ s()?.stale?.warning ?? 0 }}</span>
          <span [class.crit]="(s()?.stale?.critical ?? 0) > 0" [title]="'Older than ' + (s()?.stale?.critical_days ?? 7) + ' days'">🔥 {{ s()?.stale?.critical ?? 0 }}</span>
        </div>
        <div class="w-sub">AI values kept by analysts: <b>{{ s()?.ai_acceptance ?? '–' }}{{ s()?.ai_acceptance != null ? '%' : '' }}</b></div>
      </div>
    </section>

    @if (!api.userId()) {
      <div class="alert info">👋 Pick your profile in the top-right corner (or create one) to get your personal queue.</div>
    }

    <app-ingest-box (added)="refresh()" />

    <!-- ============ filters ============ -->
    <section class="card table-card">
      <div class="filters">
        <div class="seg" role="radiogroup" aria-label="Assignee">
          @for (o of assigneeOpts; track o.v) {
            <button [class.on]="f().assignee === o.v" (click)="set('assignee', o.v)">{{ o.l }}</button>
          }
        </div>
        <div class="seg" role="radiogroup" aria-label="Status">
          @for (o of stateOpts; track o.v) {
            <button [class.on]="f().state === o.v" (click)="set('state', o.v)">{{ o.l }}</button>
          }
        </div>
        <select [ngModel]="f().theme" (ngModelChange)="set('theme', $event)" aria-label="Theme">
          <option value="">All themes</option>
          @for (t of api.themes(); track t) { <option [value]="t">{{ t }}</option> }
        </select>
        <label class="date-range">
          <input type="date" [ngModel]="f().date_from" (ngModelChange)="set('date_from', $event)" aria-label="From" />
          <span>→</span>
          <input type="date" [ngModel]="f().date_to" (ngModelChange)="set('date_to', $event)" aria-label="To" />
        </label>
        <input class="search" type="search" placeholder="Search title, URL, summary…" [ngModel]="f().q"
               (ngModelChange)="search$.next($event)" aria-label="Search" />
        @if (filtersActive()) { <button class="link-btn" (click)="reset()">Reset</button> }
        <span class="grow"></span>
        <span class="muted small">{{ total() }} link{{ total() === 1 ? '' : 's' }}</span>
      </div>

      @if (selected().size) {
        <div class="bulkbar">
          <b>{{ selected().size }} selected</b>
          <button class="btn small" (click)="bulk('claim')">Claim</button>
          <select class="small" #assignSel (change)="bulkAssign(assignSel.value); assignSel.value = ''" aria-label="Assign to">
            <option value="">Assign to…</option>
            <option value="global">Global queue</option>
            <option value="distribute">Auto (round-robin)</option>
            @for (u of activeUsers(); track u.id) { <option [value]="u.id">{{ u.name }}</option> }
          </select>
          <button class="btn small" (click)="bulk('retry')">Retry</button>
          <button class="btn small danger ghost" (click)="bulk('reject')">Reject</button>
          <span class="sep"></span>
          <span class="muted small">Export</span>
          <button class="btn small" (click)="exportSel('pdf')">PDF</button>
          <button class="btn small" (click)="exportSel('csv')">CSV</button>
          <button class="btn small" (click)="exportSel('both')">Both</button>
          <span class="grow"></span>
          <button class="link-btn" (click)="clearSelection()">Clear selection</button>
        </div>
      }

      <!-- ============ table ============ -->
      <div class="table-wrap">
        <table class="table links">
          <thead>
            <tr>
              <th class="cb"><input type="checkbox" [checked]="allChecked()" (change)="toggleAll()" aria-label="Select all" /></th>
              <th class="sortable" (click)="sortBy('status')">Status {{ arrow('status') }}</th>
              <th class="sortable" (click)="sortBy('title')">Link {{ arrow('title') }}</th>
              <th>Themes</th>
              <th class="sortable" (click)="sortBy('assignee')">Assignee {{ arrow('assignee') }}</th>
              <th class="sortable" (click)="sortBy('created_at')">Added {{ arrow('created_at') }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (l of links(); track l.id; let i = $index) {
              <tr [class.active]="l.id === openId()" [class.cursor]="i === cursor()" (click)="openLink(l.id, i)">
                <td class="cb" (click)="$event.stopPropagation()">
                  <input type="checkbox" [checked]="selected().has(l.id)" (change)="toggle(l.id)" [attr.aria-label]="'Select ' + (l.title || l.url)" />
                </td>
                <td><span class="status" [class]="'status s-' + l.status">{{ label(l.status) }}</span></td>
                <td class="title-cell">
                  <div class="t-title">
                    @if (l.locked && l.locked_by !== api.userId()) { <span title="{{ l.locked_by_name }} is editing">🔒</span> }
                    {{ l.title || l.url }}
                  </div>
                  <div class="t-sub">
                    {{ domain(l.url) }}
                    @if (l.source === 'social') { <span class="tag">social</span> }
                    @if (l.near_duplicate_of) { <span class="tag warn" title="Very similar to #{{ l.near_duplicate_of }}">similar #{{ l.near_duplicate_of }}</span> }
                    @if (l.duplicate_of) { <span class="tag">dup of #{{ l.duplicate_of }}</span> }
                    @if (l.error && l.status !== 'error') { <span class="tag warn" [title]="l.error">retrying</span> }
                  </div>
                </td>
                <td><div class="chips nowrap">@for (t of l.themes.slice(0, 2); track t) { <span class="chip xs">{{ t }}</span> }
                  @if (l.themes.length > 2) { <span class="muted small">+{{ l.themes.length - 2 }}</span> }</div></td>
                <td>
                  @if (l.assignee_id) {
                    <span class="row gap-s"><span class="avatar xs" [style.background]="color(l.assignee_id)">{{ ini(l.assignee_name) }}</span>{{ l.assignee_name }}</span>
                  } @else { <span class="muted">Global</span> }
                </td>
                <td class="nowrap">
                  {{ rel(l.created_at) }}
                  @if (l.stale) { <span class="badge" [class]="'badge ' + l.stale">{{ l.stale === 'critical' ? 'overdue' : 'aging' }}</span> }
                </td>
                <td class="right" (click)="$event.stopPropagation()">
                  @if (!l.assignee_id && api.userId() && ['to_review','queued','processing','error'].includes(l.status)) {
                    <button class="btn small" (click)="claimOne(l)">Claim</button>
                  }
                  @if (l.status === 'error') { <button class="btn small" (click)="retryOne(l)">Retry</button> }
                </td>
              </tr>
            } @empty {
              <tr><td colspan="7" class="empty">
                @if (loading()) { <span class="spinner"></span> } @else {
                  {{ f().state === 'todo' ? 'Nothing to do here 🎉' : 'No link matches these filters.' }}
                }
              </td></tr>
            }
          </tbody>
        </table>
      </div>
      <div class="kbd-hint muted small">Shortcuts: <kbd>J</kbd>/<kbd>K</kbd> move · <kbd>Enter</kbd> open · <kbd>A</kbd> approve · <kbd>X</kbd> reject · <kbd>C</kbd> claim · <kbd>Ctrl</kbd>+<kbd>K</kbd> commands</div>
    </section>

    @if (openId(); as id) {
      <div class="panel-backdrop" (click)="closePanel()"></div>
      <app-link-panel [linkId]="id" (closed)="closePanel()" (navigate)="move($event)" (changed)="refresh()" />
    }
  `,
})
export class Dashboard implements OnInit, OnDestroy {
  api = inject(ApiService);
  toast = inject(ToastService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  f = signal<LinkFilters>(storedFilters());
  links = signal<Link[]>([]);
  total = signal(0);
  s = signal<Stats | null>(null);
  loading = signal(false);
  openId = signal<number | null>(null);
  cursor = signal(-1);
  selected = signal<Set<number>>(new Set());
  search$ = new Subject<string>();
  private refresh$ = new Subject<void>();
  private subs: Subscription[] = [];

  assigneeOpts = [{ v: 'me', l: 'Me' }, { v: 'all', l: 'All' }, { v: 'global', l: 'Global' }];
  stateOpts: { v: LinkFilters['state']; l: string }[] = [{ v: 'todo', l: 'To do' }, { v: 'done', l: 'Done' }, { v: 'all', l: 'All' }];
  activeUsers = computed(() => this.api.users().filter((u) => !u.deleted_at));
  allChecked = computed(() => this.links().length > 0 && this.links().every((l) => this.selected().has(l.id)));
  filtersActive = computed(() => {
    const f = this.f();
    return !!(f.theme || f.date_from || f.date_to || f.q);
  });

  label = (s: Link['status']) => STATUS_LABEL[s];
  rel = relativeTime;
  domain = domainOf;
  ini = initials;
  color = avatarColor;

  ngOnInit() {
    this.subs.push(
      this.search$.pipe(debounceTime(250)).subscribe((q) => this.set('q', q)),
      this.refresh$.pipe(debounceTime(400)).subscribe(() => this.refresh()),
      this.api.events$.subscribe((e) => {
        if (['link', 'links', 'lock', 'users'].includes(e.type)) this.refresh$.next();
      }),
      this.route.queryParamMap.subscribe((p) => {
        const id = Number(p.get('link'));
        this.openId.set(id || null);
      }),
    );
  }

  constructor() {
    // reload when the profile changes (also does the first load)
    effect(() => {
      this.api.userId();
      untracked(() => this.refresh());
    });
  }

  ngOnDestroy() { this.subs.forEach((s) => s.unsubscribe()); }

  refresh() {
    this.loading.set(true);
    this.api.links(this.f()).subscribe({
      next: (r) => {
        this.loading.set(false);
        this.links.set(r.items);
        this.total.set(r.total);
        const ids = new Set(r.items.map((l) => l.id));
        this.selected.update((s) => new Set([...s].filter((x) => ids.has(x))));
      },
      error: (e) => { this.loading.set(false); this.toast.error(errorMessage(e)); },
    });
    this.api.stats().subscribe({ next: (s) => this.s.set(s), error: () => {} });
  }

  set<K extends keyof LinkFilters>(key: K, value: LinkFilters[K]) {
    this.f.update((f) => ({ ...f, [key]: value }));
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(this.f())); } catch { /* ignore */ }
    this.cursor.set(-1);
    this.refresh();
  }
  reset() {
    this.f.update((f) => ({ ...f, theme: '', date_from: '', date_to: '', q: '' }));
    this.set('q', '');
  }
  quick(assignee: string) {
    this.f.update((f) => ({ ...f, assignee, state: 'todo' }));
    this.set('theme', '');
  }
  sortBy(col: string) {
    const f = this.f();
    if (f.sort === col) this.set('order', f.order === 'asc' ? 'desc' : 'asc');
    else { this.f.update((x) => ({ ...x, order: col === 'title' || col === 'assignee' ? 'asc' : 'desc' })); this.set('sort', col); }
  }
  arrow(col: string) { return this.f().sort === col ? (this.f().order === 'asc' ? '▲' : '▼') : ''; }

  // ---------------------------------------------------------- panel & keyboard
  openLink(id: number, index?: number) {
    if (index !== undefined) this.cursor.set(index);
    this.router.navigate([], { queryParams: { link: id }, queryParamsHandling: 'merge' });
  }
  closePanel() { this.router.navigate([], { queryParams: { link: null }, queryParamsHandling: 'merge' }); }
  move(delta: number) {
    const list = this.links();
    if (!list.length) return;
    const cur = this.openId() ? list.findIndex((l) => l.id === this.openId()) : this.cursor();
    const next = Math.min(list.length - 1, Math.max(0, (cur < 0 ? -1 : cur) + delta));
    if (this.openId()) {
      if (list[next] && list[next].id !== this.openId()) this.openLink(list[next].id, next);
      else if (delta > 0 && cur === list.length - 1) this.closePanel();
    } else this.cursor.set(next);
  }

  @HostListener('document:keydown', ['$event'])
  keys(e: KeyboardEvent) {
    if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')) return;
    if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.palette, .modal')) return;
    if (e.key === 'j' || e.key === 'J') { e.preventDefault(); this.move(1); }
    else if (e.key === 'k' || e.key === 'K') { e.preventDefault(); this.move(-1); }
    else if (e.key === 'Enter' && !this.openId() && this.cursor() >= 0) {
      const l = this.links()[this.cursor()];
      if (l) this.openLink(l.id, this.cursor());
    }
  }

  // ---------------------------------------------------------- selection & bulk
  toggle(id: number) {
    this.selected.update((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  toggleAll() {
    this.selected.set(this.allChecked() ? new Set() : new Set(this.links().map((l) => l.id)));
  }
  bulk(action: string, extra: { assignee_id?: number | null } = {}) {
    const ids = [...this.selected()];
    if (!this.api.userId()) return this.toast.error('Select your profile first.');
    if (action === 'reject' && !confirm(`Reject ${ids.length} link(s)?`)) return;
    this.api.bulk(ids, action, extra).subscribe({
      next: (r) => {
        const msg = `${r.done.length} updated` + (r.skipped.length ? ` · ${r.skipped.length} skipped (${r.skipped[0].reason})` : '');
        r.skipped.length ? this.toast.warning(msg) : this.toast.success(msg);
        this.selected.set(new Set());
        this.refresh();
      },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }
  clearSelection() { this.selected.set(new Set()); }
  bulkAssign(v: string) {
    if (!v) return;
    if (v === 'global') this.bulk('unassign');
    else if (v === 'distribute') this.bulk('distribute');
    else this.bulk('assign', { assignee_id: Number(v) });
  }
  exportSel(fmt: 'pdf' | 'csv' | 'both') { download(this.api.exportManyUrl([...this.selected()], fmt)); }

  claimOne(l: Link) {
    this.api.claim(l.id).subscribe({ next: () => { this.toast.success('Assigned to you'); this.refresh(); }, error: (e) => this.toast.error(errorMessage(e)) });
  }
  retryOne(l: Link) {
    this.api.retry(l.id).subscribe({ next: () => this.refresh(), error: (e) => this.toast.error(errorMessage(e)) });
  }
}

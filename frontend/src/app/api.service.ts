import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, Subject, tap } from 'rxjs';
import {
  ConnectorInfo, IngestResult, Link, LinkFilters, LinkPage, PreviewItem, RefField, RegistryEntry, Report, Settings, Stats, User,
} from './models';

const USER_KEY = 'curation.userId';

function readStored(): number | null {
  try {
    const v = localStorage.getItem(USER_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

/** All calls to the backend + shared session state (current profile, users, referential, live events). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  readonly userId = signal<number | null>(readStored());
  readonly users = signal<User[]>([]);
  readonly referential = signal<RefField[]>([]);
  readonly me = computed(() => this.users().find((u) => u.id === this.userId()) ?? null);
  /** Live events from the server (SSE). */
  readonly events$ = new Subject<{ type: string; id?: number; status?: string; user?: string | null }>();
  readonly connected = signal(false);

  private source?: EventSource;

  private get headers(): HttpHeaders {
    const id = this.userId();
    return id ? new HttpHeaders({ 'X-User-Id': String(id) }) : new HttpHeaders();
  }

  private get<T>(url: string, params?: HttpParams) {
    return this.http.get<T>(`/api${url}`, { headers: this.headers, params });
  }
  private post<T>(url: string, body: unknown = {}) {
    return this.http.post<T>(`/api${url}`, body, { headers: this.headers });
  }
  private patch<T>(url: string, body: unknown) {
    return this.http.patch<T>(`/api${url}`, body, { headers: this.headers });
  }
  private put<T>(url: string, body: unknown) {
    return this.http.put<T>(`/api${url}`, body, { headers: this.headers });
  }
  private del<T>(url: string, params?: HttpParams) {
    return this.http.delete<T>(`/api${url}`, { headers: this.headers, params });
  }

  // ---------------------------------------------------------------- session
  selectUser(id: number | null) {
    this.userId.set(id);
    try {
      if (id) localStorage.setItem(USER_KEY, String(id));
      else localStorage.removeItem(USER_KEY);
    } catch { /* storage unavailable */ }
    this.connectLive();
  }

  bootstrap() {
    this.loadUsers();
    this.getReferential().subscribe((r) => this.referential.set(r.fields));
    this.connectLive();
  }

  loadUsers() {
    this.listUsers(true).subscribe((u) => {
      this.users.set(u);
      const id = this.userId();
      if (id && !u.some((x) => x.id === id && !x.deleted_at)) this.selectUser(null);
    });
  }

  connectLive() {
    this.source?.close();
    const id = this.userId();
    this.source = new EventSource(`/api/events${id ? `?user_id=${id}` : ''}`);
    this.source.onopen = () => this.connected.set(true);
    this.source.onerror = () => this.connected.set(false);
    this.source.onmessage = (m) => {
      try {
        const data = JSON.parse(m.data);
        if (data.type === 'users' || data.type === 'presence') this.loadUsers();
        this.events$.next(data);
      } catch { /* ignore */ }
    };
  }

  // ---------------------------------------------------------------- users
  listUsers(includeDeleted = false) {
    return this.get<User[]>('/users', new HttpParams().set('include_deleted', includeDeleted));
  }
  createUser(name: string, antbox_redactor?: string) {
    return this.post<{ id: number }>('/users', { name, antbox_redactor }).pipe(tap(() => this.loadUsers()));
  }
  updateUser(id: number, body: Partial<Pick<User, 'name' | 'antbox_redactor' | 'available'>>) {
    return this.patch('/users/' + id, body).pipe(tap(() => this.loadUsers()));
  }
  deleteUser(id: number, reassign: 'global' | 'round_robin') {
    return this.del<{ moved: number; to_global: number }>('/users/' + id, new HttpParams().set('reassign', reassign))
      .pipe(tap(() => this.loadUsers()));
  }
  restoreUser(id: number) {
    return this.post('/users/' + id + '/restore').pipe(tap(() => this.loadUsers()));
  }

  // ---------------------------------------------------------------- ingestion
  preview(text: string) {
    return this.post<{ items: PreviewItem[]; new: number; duplicates: number; invalid: number }>('/ingest/preview', { text });
  }
  ingest(urls: string[], mode: string, source = 'paste') {
    return this.post<IngestResult>('/ingest', { urls, mode, source });
  }
  ingestFile(file: File, mode: string) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mode', mode);
    return this.http.post<IngestResult>('/api/ingest/file', fd, { headers: this.headers });
  }

  // ---------------------------------------------------------------- links
  links(f: LinkFilters, limit = 300): Observable<LinkPage> {
    let p = new HttpParams().set('assignee', f.assignee).set('state', f.state).set('sort', f.sort)
      .set('order', f.order).set('limit', limit);
    if (f.doc_type) p = p.set('doc_type', f.doc_type);
    if (f.date_from) p = p.set('date_from', f.date_from);
    if (f.date_to) p = p.set('date_to', f.date_to);
    if (f.q) p = p.set('q', f.q);
    return this.get<LinkPage>('/links', p);
  }
  link(id: number) { return this.get<Link>('/links/' + id); }
  linkText(id: number) { return this.get<{ text: string }>(`/links/${id}/text`); }
  saveLink(id: number, body: Partial<Pick<Link, 'title' | 'summary_html' | 'metadata' | 'info'>>) {
    return this.patch<Link & { rejected_values: Record<string, string[]> }>('/links/' + id, body);
  }
  approve(id: number) { return this.post<{ link: Link; registry_id: number; connector_rows: number }>(`/links/${id}/approve`); }
  reject(id: number, reason = '') { return this.post(`/links/${id}/reject`, { reason }); }
  reopen(id: number) { return this.post(`/links/${id}/reopen`); }
  claim(id: number) { return this.post(`/links/${id}/claim`); }
  assign(id: number, assignee_id: number | null) { return this.post(`/links/${id}/assign`, { assignee_id }); }
  retry(id: number) { return this.post(`/links/${id}/retry`); }
  lock(id: number) { return this.post<{ ok: boolean; locked_by?: string }>(`/links/${id}/lock`); }
  unlock(id: number) { return this.del(`/links/${id}/lock`); }
  bulk(ids: number[], action: string, extra: { assignee_id?: number | null; reason?: string } = {}) {
    return this.post<{ done: number[]; skipped: { id: number; reason: string }[] }>('/links/bulk', { ids, action, ...extra });
  }

  // ---------------------------------------------------------------- files (plain links: work with <a href>)
  pdfUrl(id: number) { return `/api/links/${id}/pdf`; }
  exportUrl(id: number, format: 'pdf' | 'csv' | 'both') { return `/api/links/${id}/export?format=${format}`; }
  exportManyUrl(ids: number[], format: 'pdf' | 'csv' | 'both') { return `/api/export?ids=${ids.join(',')}&format=${format}`; }
  exportApprovedUrl(format: 'pdf' | 'csv' | 'both') { return `/api/export?approved=true&format=${format}`; }
  registryFileUrl(id: number, kind: 'pdf' | 'csv') { return `/api/registry/${id}/${kind}`; }
  reportFileUrl(id: number, kind: 'pdf' | 'html' | 'md') { return `/api/reports/${id}/${kind}`; }

  // ---------------------------------------------------------------- AntBox connector file
  connector() { return this.get<ConnectorInfo>('/connector'); }
  archiveConnector() { return this.post<{ archived: string }>('/connector/archive'); }
  connectorCsvUrl() { return '/api/connector/csv'; }
  connectorPackageUrl() { return '/api/connector/package'; }
  connectorArchiveUrl(name: string) { return `/api/connector/archive/${encodeURIComponent(name)}`; }

  // ---------------------------------------------------------------- registry & reports
  registry(q = '', date_from = '', date_to = '', operator = '') {
    let p = new HttpParams();
    if (q) p = p.set('q', q);
    if (date_from) p = p.set('date_from', date_from);
    if (date_to) p = p.set('date_to', date_to);
    if (operator) p = p.set('operator', operator);
    return this.get<RegistryEntry[]>('/registry', p);
  }
  verifyRegistry() {
    return this.get<{ ok: boolean; entries: number; problems: { id: number; problem: string }[]; last_hash: string }>('/registry/verify');
  }
  reports() { return this.get<Report[]>('/reports'); }
  report(id: number) { return this.get<Report>('/reports/' + id); }
  createReport(body: { period: string; start?: string; end?: string; granularity: string; title?: string }) {
    return this.post<Report>('/reports', body);
  }

  // ---------------------------------------------------------------- stats & settings
  stats() { return this.get<Stats>('/stats'); }
  getReferential() { return this.get<{ file: string; fields: RefField[] }>('/referential'); }
  uploadReferential(file: File) {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<{ fields: number; values: number }>('/api/referential', fd)
      .pipe(tap(() => this.getReferential().subscribe((r) => this.referential.set(r.fields))));
  }
  getSettings() { return this.get<Settings>('/settings'); }
  setSocial(enabled: boolean) { return this.put<Settings['social']>('/settings/social', { enabled }); }
  socialLogin(network: string) { return this.post<Settings['social']>(`/settings/social/login/${network}`); }
  testLlm() { return this.post<{ ok: boolean; mode: string; answer: string }>('/settings/llm/test'); }
  testNotify() { return this.post<{ sent: string }>('/settings/notify/test'); }
}

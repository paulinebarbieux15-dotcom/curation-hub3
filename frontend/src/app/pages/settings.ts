import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api.service';
import { Settings as SettingsModel } from '../models';
import { ProfilesManager } from '../ui/profiles-manager';
import { ToastService } from '../ui/toast.service';
import { errorMessage, formatDate } from '../ui/util';

@Component({
  selector: 'app-settings',
  imports: [FormsModule, ProfilesManager],
  template: `
    <div class="page-head"><div><h1>Settings</h1><p class="muted">Team configuration. Secrets (LLM key, social credentials) stay in backend/.env.</p></div></div>

    <div class="settings-grid">
      <section class="card span-2" id="profiles">
        <h2>Profiles</h2>
        <p class="muted small">Auto-distribution is a capped round-robin among <b>active</b> profiles
          (max {{ s()?.distribution?.queue_cap_per_user || '∞' }} open items each; overflow goes to the global queue).</p>
        <app-profiles-manager />
      </section>

      <section class="card">
        <h2>Themes</h2>
        <p class="muted small">Controlled list the AI picks from (1–3 per link). Dashboard only — never exported.</p>
        <div class="chips">
          @for (t of themes(); track t) {
            <span class="chip">{{ t }} <button class="chip-x" (click)="removeTheme(t)" [attr.aria-label]="'Remove ' + t">×</button></span>
          }
        </div>
        <form class="row gap-s mt-s" (ngSubmit)="addTheme()">
          <input name="t" [(ngModel)]="newTheme" placeholder="New theme" />
          <button class="btn" [disabled]="!newTheme.trim()">Add</button>
          <span class="grow"></span>
          <button type="button" class="btn primary" [disabled]="!themesDirty()" (click)="saveThemes()">Save themes</button>
        </form>
      </section>

      <section class="card">
        <h2>AntBox referential</h2>
        <p class="small">File: <code>{{ s()?.referential?.file }}</code> · {{ api.referential().length }} fields</p>
        <table class="table compact">
          <thead><tr><th>Field</th><th>Values</th><th>Type</th><th>AI</th></tr></thead>
          <tbody>
            @for (f of api.referential(); track f.name) {
              <tr><td>{{ f.name }} @if (f.required) { <span class="req">required</span> }</td><td>{{ f.values.length }}</td>
                <td>{{ f.multi ? 'multi' : 'single' }}</td><td>{{ f.ai ? 'yes' : 'manual' }}</td></tr>
            }
          </tbody>
        </table>
        <label class="btn small mt-s file-btn">Import a new referential (CSV / TXT)
          <input type="file" accept=".csv,.txt,.tsv" hidden (change)="uploadRef($event)" /></label>
      </section>

      <section class="card">
        <h2>Shared social account</h2>
        <p class="muted small">LinkedIn, X, Instagram, Facebook posts are captured with the team account. The session (cookies)
          is kept and the app logs back in by itself with the credentials from .env when it expires.</p>
        @if (s()?.social; as so) {
          <label class="switch big">
            <input type="checkbox" [checked]="so.enabled" (change)="toggleSocial(!so.enabled)" />
            <span>{{ so.enabled ? 'Social capture ON' : 'Social capture OFF' }}</span>
          </label>
          <p class="small">Session: @if (so.session_present) { <b class="ok-text">saved</b> (updated {{ date(so.session_updated_at) }}) } @else { <b class="warn-text">none yet</b> }</p>
          <table class="table compact">
            <thead><tr><th>Network</th><th>Credentials</th><th>Auto 2FA</th><th>Last login</th><th></th></tr></thead>
            <tbody>
              @for (n of so.networks; track n.name) {
                <tr>
                  <td class="cap">{{ n.name }}</td>
                  <td>{{ n.credentials ? '✔ in .env' : '—' }}</td>
                  <td>{{ n.auto_2fa ? '✔' : '—' }}</td>
                  <td class="small">{{ n.last_login ? date(n.last_login.at) + ' · ' + n.last_login.result : '—' }}</td>
                  <td><button class="btn small" [disabled]="!n.credentials || loginBusy() === n.name" (click)="login(n.name)">
                    {{ loginBusy() === n.name ? 'Logging in…' : 'Test login' }}</button></td>
                </tr>
              }
            </tbody>
          </table>
          <p class="muted small">Pace: one social capture every 20–40 s, max {{ s()?.worker?.social_daily_cap }} per day ({{ s()?.worker?.social_today }} today).
            Manual fallback: <code>python -m app.social_login</code></p>
        }
      </section>

      <section class="card">
        <h2>Internal LLM</h2>
        @if (s()?.llm; as l) {
          <p class="small">Mode: <b>{{ l.fake ? 'simulated (LLM_FAKE=true)' : 'live' }}</b>
            @if (!l.fake) { · {{ l.host || 'LLM_URL not set' }} · model {{ l.model || 'default' }} }</p>
          <p class="small">High-certainty policy: values kept only with confidence ≥ <b>{{ (l.confidence_threshold * 100).toFixed(0) }}%</b>
            {{ l.require_evidence ? 'and a verbatim evidence quote found in the article' : '' }}; otherwise the field is left empty.</p>
        }
        <button class="btn" (click)="testLlm()">Test connection</button>
        @if (llmAnswer()) { <span class="small mono"> → {{ llmAnswer() }}</span> }
      </section>

      <section class="card">
        <h2>Google Chat</h2>
        <p class="small">Webhook: <b>{{ s()?.notifications?.google_chat ? 'configured' : 'not configured (GOOGLE_CHAT_WEBHOOK in .env)' }}</b>
          @if (s()?.notifications?.google_chat) { · daily digest at {{ s()?.notifications?.digest_hour }}:00 }</p>
        <button class="btn" [disabled]="!s()?.notifications?.google_chat" (click)="testNotify()">Send digest now</button>
      </section>

      <section class="card">
        <h2>Job queue</h2>
        @if (s()?.worker; as w) {
          <p class="small">{{ w.pending }} pending · {{ w.running }} running · {{ w.failed }} failed</p>
        }
        <p class="muted small">CSV export: separator “{{ s()?.csv?.separator }}”, multi-values “{{ s()?.csv?.multi_separator }}”, values as {{ s()?.csv?.values }}.</p>
      </section>
    </div>
  `,
})
export class Settings implements OnInit {
  api = inject(ApiService);
  toast = inject(ToastService);
  s = signal<SettingsModel | null>(null);
  themes = signal<string[]>([]);
  themesDirty = signal(false);
  newTheme = '';
  loginBusy = signal<string | null>(null);
  llmAnswer = signal('');
  date = (d: string | null) => formatDate(d);

  ngOnInit() { this.load(); }

  load() {
    this.api.getSettings().subscribe((s) => {
      this.s.set(s);
      this.themes.set(s.themes);
      this.themesDirty.set(false);
    });
  }

  addTheme() {
    const t = this.newTheme.trim();
    if (t && !this.themes().some((x) => x.toLowerCase() === t.toLowerCase())) {
      this.themes.update((l) => [...l, t]);
      this.themesDirty.set(true);
    }
    this.newTheme = '';
  }
  removeTheme(t: string) {
    this.themes.update((l) => l.filter((x) => x !== t));
    this.themesDirty.set(true);
  }
  saveThemes() {
    this.api.setThemes(this.themes()).subscribe({
      next: () => { this.themesDirty.set(false); this.toast.success('Themes saved'); },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  uploadRef(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0];
    input.value = '';
    if (!f) return;
    this.api.uploadReferential(f).subscribe({
      next: (r) => { this.toast.success(`Referential imported: ${r.fields} fields, ${r.values} values`); this.load(); },
      error: (e) => this.toast.error(errorMessage(e)),
    });
  }

  toggleSocial(on: boolean) {
    this.api.setSocial(on).subscribe({ next: () => this.load(), error: (e) => this.toast.error(errorMessage(e)) });
  }
  login(name: string) {
    this.loginBusy.set(name);
    this.api.socialLogin(name).subscribe({
      next: () => { this.loginBusy.set(null); this.toast.success(`Logged in to ${name}`); this.load(); },
      error: (e) => { this.loginBusy.set(null); this.toast.error(errorMessage(e)); this.load(); },
    });
  }
  testLlm() {
    this.llmAnswer.set('…');
    this.api.testLlm().subscribe({
      next: (r) => this.llmAnswer.set(`OK (${r.mode}): ${r.answer}`),
      error: (e) => this.llmAnswer.set('Error: ' + errorMessage(e)),
    });
  }
  testNotify() {
    this.api.testNotify().subscribe({ next: () => this.toast.success('Digest sent to Google Chat'), error: (e) => this.toast.error(errorMessage(e)) });
  }
}

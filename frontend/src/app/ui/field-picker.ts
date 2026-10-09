import { Component, computed, input, output, signal } from '@angular/core';
import { AiDecision, RefField } from '../models';
import { display } from './util';

/** AntBox field editor: chips (with AI confidence badges) + searchable closed list. */
@Component({
  selector: 'app-field-picker',
  template: `
    <div class="field" [class.missing]="field().required && !values().length">
      <div class="field-head">
        <span class="field-name">{{ field().name }}</span>
        @if (field().required) { <span class="req" title="Required before approval">required</span> }
        <span class="muted small">{{ field().multi ? 'multi' : 'single' }}</span>
        @if (!field().ai) { <span class="muted small" title="Never filled by the AI">manual</span> }
      </div>
      <div class="chips">
        @for (v of values(); track v) {
          <span class="chip" [class.ai]="aiFor(v)" [title]="aiTitle(v)">
            {{ show(v) }}
            @if (aiFor(v); as d) { <span class="conf">AI {{ (d.confidence * 100).toFixed(0) }}%</span> }
            @if (!disabled()) { <button type="button" class="chip-x" (click)="remove(v)" [attr.aria-label]="'Remove ' + show(v)">×</button> }
          </span>
        }
        @if (!values().length) { <span class="empty-hint">{{ field().ai ? 'Left empty (AI not certain)' : 'Empty' }}</span> }
      </div>
      @if (dropped().length && !disabled()) {
        <div class="suggestions">
          <span class="muted small">Uncertain AI suggestions:</span>
          @for (d of dropped(); track d.value) {
            <button type="button" class="chip ghost" [title]="d.reason + (d.evidence ? ' — “' + d.evidence + '”' : '')"
                    (click)="add(d.value)">+ {{ show(d.value) }} <span class="conf low">{{ (d.confidence * 100).toFixed(0) }}%</span></button>
          }
        </div>
      }
      @if (!disabled() && (field().multi || !values().length)) {
        <div class="picker">
          <input type="search" [placeholder]="'Add ' + field().name.toLowerCase() + '…'" [value]="query()"
                 (input)="query.set($any($event.target).value); open.set(true)" (focus)="open.set(true)"
                 (blur)="close()" (keydown.enter)="$event.preventDefault(); pickFirst()" (keydown.escape)="open.set(false)" />
          @if (open() && matches().length) {
            <ul class="dropdown" role="listbox">
              @for (m of matches(); track m.label) {
                <li role="option" (mousedown)="$event.preventDefault(); add(m.label)">{{ m.display }}</li>
              }
              @if (more()) { <li class="muted small">Type to narrow down ({{ more() }} more)…</li> }
            </ul>
          }
        </div>
      }
    </div>
  `,
})
export class FieldPicker {
  field = input.required<RefField>();
  values = input<string[]>([]);
  decisions = input<AiDecision[]>([]);
  disabled = input(false);
  changed = output<string[]>();

  query = signal('');
  open = signal(false);

  private filtered = computed(() => {
    const q = this.query().toLowerCase().trim();
    const chosen = new Set(this.values());
    return this.field().values.filter((v) => !chosen.has(v.label) && (!q || v.display.toLowerCase().includes(q)));
  });
  matches = computed(() => this.filtered().slice(0, 40));
  more = computed(() => Math.max(0, this.filtered().length - 40));
  dropped = computed(() => {
    const chosen = new Set(this.values().map((v) => display(v).toLowerCase()));
    const known = new Set(this.field().values.map((v) => v.display.toLowerCase()));
    return this.decisions().filter((d) => !d.kept && known.has(display(d.value).toLowerCase())
      && !chosen.has(display(d.value).toLowerCase()));
  });

  show(v: string) { return display(v); }

  aiFor(v: string): AiDecision | undefined {
    return this.decisions().find((d) => d.kept && display(d.value) === display(v));
  }
  aiTitle(v: string) {
    const d = this.aiFor(v);
    return d ? `AI confidence ${(d.confidence * 100).toFixed(0)}% — evidence: “${d.evidence}”` : 'Set by an analyst';
  }

  add(labelOrDisplay: string) {
    const f = this.field();
    const ref = f.values.find((v) => v.label === labelOrDisplay || v.display.toLowerCase() === display(labelOrDisplay).toLowerCase());
    if (!ref) return;
    const next = f.multi ? [...this.values().filter((x) => x !== ref.label), ref.label] : [ref.label];
    this.query.set('');
    this.open.set(false);
    this.changed.emit(next);
  }
  pickFirst() {
    const m = this.matches()[0];
    if (m) this.add(m.label);
  }
  remove(v: string) { this.changed.emit(this.values().filter((x) => x !== v)); }
  close() { setTimeout(() => this.open.set(false), 120); }
}

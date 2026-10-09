import { Component, computed, input } from '@angular/core';

/** Tiny SVG bar chart (7-day velocity). */
@Component({
  selector: 'app-sparkline',
  template: `
    <svg [attr.viewBox]="'0 0 ' + w + ' ' + h" [attr.width]="w" [attr.height]="h" role="img"
         [attr.aria-label]="label()">
      @for (b of bars(); track $index) {
        <rect [attr.x]="b.x" [attr.y]="h - b.hTeam" [attr.width]="bw" [attr.height]="b.hTeam" rx="2" class="spark-team">
          <title>{{ b.day }}: team {{ b.team }}, you {{ b.me }}</title>
        </rect>
        <rect [attr.x]="b.x + bw * 0.25" [attr.y]="h - b.hMe" [attr.width]="bw * 0.5" [attr.height]="b.hMe" rx="1.5" class="spark-me" />
      }
    </svg>
  `,
  styles: [`:host{display:block} .spark-team{fill:var(--accent-soft)} .spark-me{fill:var(--accent)}`],
})
export class Sparkline {
  team = input<number[]>([]);
  me = input<number[]>([]);
  days = input<string[]>([]);
  w = 140;
  h = 40;
  bw = 14;
  label = computed(() => `Approvals per day, last 7 days: ${this.team().join(', ')}`);
  bars = computed(() => {
    const t = this.team();
    const max = Math.max(1, ...t);
    const step = this.w / Math.max(1, t.length);
    return t.map((v, i) => ({
      x: i * step + (step - this.bw) / 2,
      hTeam: Math.max(2, (v / max) * (this.h - 2)),
      hMe: Math.max(this.me()[i] ? 2 : 0, ((this.me()[i] || 0) / max) * (this.h - 2)),
      team: v,
      me: this.me()[i] || 0,
      day: this.days()[i] || '',
    }));
  });
}

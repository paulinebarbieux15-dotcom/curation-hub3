import { Component, ElementRef, effect, input, output, viewChild } from '@angular/core';

/** Lightweight WYSIWYG editor (contenteditable + toolbar). The backend sanitizes the HTML. */
@Component({
  selector: 'app-rich-editor',
  template: `
    <div class="rte" [class.disabled]="disabled()">
      <div class="rte-toolbar" role="toolbar" aria-label="Formatting">
        <button type="button" title="Bold (Ctrl+B)" (mousedown)="cmd($event, 'bold')"><b>B</b></button>
        <button type="button" title="Italic (Ctrl+I)" (mousedown)="cmd($event, 'italic')"><i>I</i></button>
        <button type="button" title="Underline (Ctrl+U)" (mousedown)="cmd($event, 'underline')"><u>U</u></button>
        <span class="sep"></span>
        <button type="button" title="Heading" (mousedown)="cmd($event, 'formatBlock', 'h4')">H</button>
        <button type="button" title="Paragraph" (mousedown)="cmd($event, 'formatBlock', 'p')">¶</button>
        <button type="button" title="Bulleted list" (mousedown)="cmd($event, 'insertUnorderedList')">•≡</button>
        <button type="button" title="Numbered list" (mousedown)="cmd($event, 'insertOrderedList')">1≡</button>
        <button type="button" title="Quote" (mousedown)="cmd($event, 'formatBlock', 'blockquote')">❝</button>
        <span class="sep"></span>
        <button type="button" title="Link" (mousedown)="link($event)">🔗</button>
        <button type="button" title="Clear formatting" (mousedown)="cmd($event, 'removeFormat')">⌫</button>
      </div>
      <div #area class="rte-area" [attr.contenteditable]="!disabled()" (input)="emit()" (blur)="emit()"
           role="textbox" aria-multiline="true" aria-label="Summary"></div>
    </div>
  `,
})
export class RichEditor {
  value = input<string>('');
  disabled = input(false);
  changed = output<string>();
  private area = viewChild.required<ElementRef<HTMLDivElement>>('area');
  private last = '';

  constructor() {
    effect(() => {
      const v = this.value() || '';
      const el = this.area().nativeElement;
      if (v !== this.last && v !== el.innerHTML) {
        el.innerHTML = v;
        this.last = v;
      }
    });
  }

  cmd(ev: Event, command: string, arg?: string) {
    ev.preventDefault();
    if (this.disabled()) return;
    this.area().nativeElement.focus();
    document.execCommand(command, false, arg);
    this.emit();
  }

  link(ev: Event) {
    ev.preventDefault();
    const url = prompt('Link URL (https://...)');
    if (url) this.cmd(ev, 'createLink', url);
  }

  emit() {
    const html = this.area().nativeElement.innerHTML;
    if (html !== this.last) {
      this.last = html;
      this.changed.emit(html);
    }
  }
}

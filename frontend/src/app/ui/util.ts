import { HttpErrorResponse } from '@angular/common/http';

export function errorMessage(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    const d = e.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => x.msg).join(', ');
    if (e.status === 0) return 'Server unreachable — is the API running?';
    return `${e.status} ${e.statusText}`;
  }
  return String(e);
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('en-GB', withTime
    ? { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { day: '2-digit', month: 'short', year: 'numeric' });
}

export function initials(name: string | null | undefined): string {
  if (!name) return '?';
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('');
}

const PALETTE = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#db2777', '#7c3aed', '#2563eb', '#dc2626', '#65a30d', '#0d9488'];
export function avatarColor(id: number | null | undefined): string {
  return id ? PALETTE[id % PALETTE.length] : '#94a3b8';
}

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** Same rules as the backend (instant client-side validation for manual input). */
export function looksLikeUrl(value: string): boolean {
  const v = value.trim();
  if (!v || /\s/.test(v)) return false;
  try {
    const u = new URL(/^[a-z]+:\/\//i.test(v) ? v : 'https://' + v);
    return (u.protocol === 'http:' || u.protocol === 'https:') && /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(u.hostname);
  } catch {
    return false;
  }
}

export function display(label: string): string {
  return label.split(/\s+/).join(' ').replaceAll('\\', ' > ');
}

export function download(url: string) {
  const a = document.createElement('a');
  a.href = url;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

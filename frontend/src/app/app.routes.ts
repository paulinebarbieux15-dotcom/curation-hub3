import { Routes } from '@angular/router';
import { Dashboard } from './pages/dashboard';
import { Registry } from './pages/registry';
import { Reports } from './pages/reports';
import { Settings } from './pages/settings';

export const routes: Routes = [
  { path: '', component: Dashboard, title: 'Dashboard · Curation Hub' },
  { path: 'registry', component: Registry, title: 'Registry · Curation Hub' },
  { path: 'reports', component: Reports, title: 'Reports · Curation Hub' },
  { path: 'settings', component: Settings, title: 'Settings · Curation Hub' },
  { path: '**', redirectTo: '' },
];

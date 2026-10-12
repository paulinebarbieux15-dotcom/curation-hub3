import { Routes } from '@angular/router';
import { Dashboard } from './pages/dashboard';
import { Registry } from './pages/registry';
import { Reports } from './pages/reports';
import { Settings } from './pages/settings';

export const routes: Routes = [
  { path: '', component: Dashboard, title: 'Dashboard · ETCA' },
  { path: 'registry', component: Registry, title: 'Registry · ETCA' },
  { path: 'reports', component: Reports, title: 'Reports · ETCA' },
  { path: 'settings', component: Settings, title: 'Settings · ETCA' },
  { path: '**', redirectTo: '' },
];

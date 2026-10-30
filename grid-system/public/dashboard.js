'use strict';
const $ = (id) => document.getElementById(id);
const fmt = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—');

fetch('/api/me').then((r) => (r.ok ? r.json() : Promise.reject())).then(({ user }) => {
  $('welcome').textContent = `Welcome, ${user.name}`;
  $('f-name').textContent = user.name;
  $('f-username').textContent = user.username;
  $('f-email').textContent = user.email || 'Not provided';
  $('f-created').textContent = fmt(user.createdAt);
}).catch(() => window.location.assign('/'));

$('signout').addEventListener('click', async () => {
  try { await fetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch {}
  window.location.assign('/');
});

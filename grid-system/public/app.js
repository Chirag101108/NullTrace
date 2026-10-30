'use strict';

const $ = (id) => document.getElementById(id);

/* ---------- Tabs ---------- */
const tabs = { signin: $('tab-signin'), signup: $('tab-signup') };
const views = { signin: $('view-signin'), signup: $('view-signup') };

function showTab(name, { focus = false } = {}) {
  for (const key of Object.keys(tabs)) {
    const active = key === name;
    tabs[key].setAttribute('aria-selected', String(active));
    tabs[key].tabIndex = active ? 0 : -1;
    views[key].hidden = !active;
  }
  document.title = name === 'signin' ? 'Sign in to Grid System' : 'Create a Grid System account';
  if (focus) (name === 'signin' ? $('si-username') : $('su-name')).focus();
}

tabs.signin.addEventListener('click', () => showTab('signin', { focus: true }));
tabs.signup.addEventListener('click', () => showTab('signup', { focus: true }));
for (const tab of Object.values(tabs)) {
  tab.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const next = tab === tabs.signin ? 'signup' : 'signin';
    showTab(next);
    tabs[next].focus();
  });
}

/* ---------- Show / hide password ---------- */
for (const button of document.querySelectorAll('[data-reveal]')) {
  button.addEventListener('click', () => {
    const input = $(button.dataset.reveal);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.textContent = show ? 'Hide' : 'Show';
    button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    button.setAttribute('aria-pressed', String(show));
  });
}

/* ---------- Helpers ---------- */
function notify(el, kind, message) {
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
}

function clearNotice(el, form) {
  el.hidden = true;
  el.textContent = '';
  for (const input of form.querySelectorAll('[aria-invalid]')) input.removeAttribute('aria-invalid');
}

async function post(route, payload) {
  let response;
  try {
    response = await fetch(route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  } catch {
    throw Object.assign(new Error('Can’t reach the app. Make sure it is still running in Terminal, then try again.'), { field: null });
  }
  let data = {};
  try { data = await response.json(); } catch { /* non-JSON error */ }
  if (!response.ok) throw Object.assign(new Error(data.error || 'Something went wrong. Try again.'), { field: data.field || null });
  return data;
}

const FIELD_IDS = {
  name: 'su-name', email: 'su-email', username: 'su-username', password: 'su-password', confirmPassword: 'su-confirm'
};

/* ---------- Sign in ---------- */
const signinForm = $('signin-form');
const signinNotice = $('signin-notice');

signinForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearNotice(signinNotice, signinForm);
  const button = $('signin-submit');
  const data = Object.fromEntries(new FormData(signinForm));
  if (!data.username.trim() || !data.password) {
    notify(signinNotice, 'error', 'Enter your username and password.');
    (data.username.trim() ? $('si-password') : $('si-username')).focus();
    return;
  }
  button.disabled = true;
  button.textContent = 'Signing in…';
  try {
    const result = await post('/api/login', data);
    window.location.assign(result.redirect || 'https://www.google.com/');
  } catch (error) {
    notify(signinNotice, 'error', error.message);
    $('si-password').value = '';
    $('si-password').focus();
    button.disabled = false;
    button.textContent = 'Sign in';
  }
});

/* ---------- Sign up ---------- */
const signupForm = $('signup-form');
const signupNotice = $('signup-notice');

signupForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearNotice(signupNotice, signupForm);
  const button = $('signup-submit');
  const data = Object.fromEntries(new FormData(signupForm));

  if (data.password !== data.confirmPassword) {
    $('su-confirm').setAttribute('aria-invalid', 'true');
    notify(signupNotice, 'error', 'The two passwords don’t match.');
    $('su-confirm').focus();
    return;
  }

  button.disabled = true;
  button.textContent = 'Creating account…';
  try {
    const result = await post('/api/signup', data);
    signupForm.reset();
    showTab('signin');
    $('si-username').value = result.username;
    notify(signinNotice, 'success', 'Account created. Sign in to continue.');
    $('si-password').focus();
  } catch (error) {
    const field = $(FIELD_IDS[error.field] || '');
    if (field) { field.setAttribute('aria-invalid', 'true'); field.focus(); }
    notify(signupNotice, 'error', error.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Create account';
  }
});

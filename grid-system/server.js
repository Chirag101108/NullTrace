'use strict';

/*
 * Grid System by Null Trace — local sign-up / sign-in server.
 * Node built-ins only. No packages to install.
 *
 * Users are stored in .local-data/users.json (pretty-printed, human-readable).
 * Passwords are never stored: only a salted scrypt hash.
 */

const http = require('node:http');
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const { promisify } = require('node:util');

const scrypt = promisify(crypto.scrypt);

const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, 'public');
const VIEWS_DIR = path.join(ROOT, 'views');
const DATA_DIR = path.join(ROOT, '.local-data');
const USERS_FILE = path.join(DATA_DIR, 'users.json');

const SCRYPT_PARAMS = { N: 16384, r: 8, p: 1, keylen: 64 };
const SESSION_COOKIE = 'gs_session';
const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8 hours
const MAX_FAILED_LOGINS = 5;
const LOCKOUT_MS = 15 * 60 * 1000;

// Demo accounts created the first time the app runs (only if users.json does not exist yet).
// Delete or edit them in .local-data/users.json whenever you like.
const SEED_ACCOUNTS = [
  { name: 'Admin 1', username: 'admin1', password: '121' },
  { name: 'Admin 2', username: 'Admin-2', password: '122' },
  { name: 'Admin 3', username: 'Admin - 3', password: '123' },
  { name: 'Admin 4', username: 'Admin - 4', password: '124' }
];

/* ------------------------------------------------------------------ */
/* Storage                                                             */
/* ------------------------------------------------------------------ */

let db = { version: 1, users: [] };
let writeChain = Promise.resolve();

const usernameKey = (username) => username.trim().toLowerCase();

async function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = await scrypt(password, salt, SCRYPT_PARAMS.keylen, SCRYPT_PARAMS);
  return {
    algo: 'scrypt',
    N: SCRYPT_PARAMS.N,
    r: SCRYPT_PARAMS.r,
    p: SCRYPT_PARAMS.p,
    salt: salt.toString('base64'),
    hash: hash.toString('base64')
  };
}

async function verifyPassword(password, stored) {
  const expected = Buffer.from(stored.hash, 'base64');
  const actual = await scrypt(password, Buffer.from(stored.salt, 'base64'), expected.length, {
    N: stored.N, r: stored.r, p: stored.p
  });
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

// A throwaway hash so logins for unknown usernames cost the same time as real ones.
let dummyPassword;

function saveDb() {
  const snapshot = `${JSON.stringify(db, null, 2)}\n`;
  const write = async () => {
    const tmp = `${USERS_FILE}.tmp`;
    await fsp.writeFile(tmp, snapshot, { mode: 0o600 });
    await fsp.rename(tmp, USERS_FILE); // atomic swap: a crash never leaves a half-written file
  };
  writeChain = writeChain.catch(() => {}).then(write);
  return writeChain;
}

async function loadDb() {
  await fsp.mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
  dummyPassword = await hashPassword(crypto.randomBytes(12).toString('hex'));
  try {
    const parsed = JSON.parse(await fsp.readFile(USERS_FILE, 'utf8'));
    if (!parsed || !Array.isArray(parsed.users)) throw new Error('users.json must look like { "version": 1, "users": [ ... ] }');
    db = parsed;
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw new Error(`Could not read ${USERS_FILE}: ${error.message}\nFix or delete that file and start again.`);
    }
    const now = new Date().toISOString();
    for (const seed of SEED_ACCOUNTS) {
      db.users.push({
        id: crypto.randomUUID(),
        name: seed.name,
        email: null,
        username: seed.username,
        usernameKey: usernameKey(seed.username),
        role: 'admin',
        password: await hashPassword(seed.password),
        createdAt: now,
        lastLoginAt: null
      });
    }
    await saveDb();
  }
}

const publicUser = (user) => ({
  id: user.id,
  name: user.name,
  username: user.username,
  email: user.email,
  role: user.role,
  createdAt: user.createdAt,
  lastLoginAt: user.lastLoginAt
});

/* ------------------------------------------------------------------ */
/* Sessions and login throttling (in memory; reset when the app stops) */
/* ------------------------------------------------------------------ */

const sessions = new Map(); // token -> { userId, expires }
const failures = new Map(); // "ip|username" -> { count, lockedUntil, last }

function parseCookies(header = '') {
  const out = {};
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function sessionUser(req) {
  const token = parseCookies(req.headers.cookie)[SESSION_COOKIE];
  if (!token) return null;
  const session = sessions.get(token);
  if (!session) return null;
  if (session.expires < Date.now()) { sessions.delete(token); return null; }
  return db.users.find((u) => u.id === session.userId) || null;
}

function startSession(res, user) {
  const token = crypto.randomBytes(32).toString('hex');
  sessions.set(token, { userId: user.id, expires: Date.now() + SESSION_TTL_MS });
  return `${SESSION_COOKIE}=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_TTL_MS / 1000}`;
}

setInterval(() => {
  const now = Date.now();
  for (const [token, s] of sessions) if (s.expires < now) sessions.delete(token);
  for (const [key, f] of failures) if (f.last + LOCKOUT_MS < now) failures.delete(key);
}, 5 * 60 * 1000).unref();

/* ------------------------------------------------------------------ */
/* HTTP helpers                                                        */
/* ------------------------------------------------------------------ */

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
};

function sendJson(res, status, payload, headers = {}) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...SECURITY_HEADERS,
    ...headers
  });
  res.end(JSON.stringify(payload));
}

function redirect(res, location) {
  res.writeHead(302, { Location: location, 'Cache-Control': 'no-store', ...SECURITY_HEADERS });
  res.end();
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const fail = (status, message) => reject(Object.assign(new Error(message), { status }));
    if (!String(req.headers['content-type'] || '').toLowerCase().startsWith('application/json')) {
      return fail(415, 'Send the request as JSON.');
    }
    const origin = req.headers.origin;
    if (origin && origin !== `http://${req.headers.host}`) return fail(403, 'Request blocked: unexpected origin.');

    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > 10_000) { fail(413, 'Request is too large.'); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch { fail(400, 'The request was not valid JSON.'); }
    });
    req.on('error', reject);
  });
}

const str = (value) => (typeof value === 'string' ? value : '');

/* ------------------------------------------------------------------ */
/* API handlers                                                        */
/* ------------------------------------------------------------------ */

async function handleSignup(req, res) {
  const input = await readJson(req);
  const name = str(input.name).trim();
  const email = str(input.email).trim().toLowerCase();
  const username = str(input.username).trim();
  const password = str(input.password);
  const confirmPassword = str(input.confirmPassword);

  if (!name || !email || !username || !password || !confirmPassword) {
    return sendJson(res, 400, { error: 'Fill in every field.', field: 'form' });
  }
  if (name.length > 100) return sendJson(res, 400, { error: 'Name must be 100 characters or fewer.', field: 'name' });
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return sendJson(res, 400, { error: 'Enter a valid email address.', field: 'email' });
  }
  if (!/^[A-Za-z0-9._-]{3,32}$/.test(username)) {
    return sendJson(res, 400, { error: 'Username must be 3–32 characters: letters, numbers, dots, dashes or underscores.', field: 'username' });
  }
  if (password.length < 8 || password.length > 128) {
    return sendJson(res, 400, { error: 'Password must be 8–128 characters.', field: 'password' });
  }
  if (password.toLowerCase() === username.toLowerCase()) {
    return sendJson(res, 400, { error: 'Password can’t be the same as your username.', field: 'password' });
  }
  if (password !== confirmPassword) {
    return sendJson(res, 400, { error: 'The two passwords don’t match.', field: 'confirmPassword' });
  }

  const passwordRecord = await hashPassword(password);

  // Check-and-insert runs synchronously, so two simultaneous sign-ups can't both claim a name.
  const key = usernameKey(username);
  if (db.users.some((u) => u.usernameKey === key)) {
    return sendJson(res, 409, { error: 'That username is taken. Try another.', field: 'username' });
  }
  if (db.users.some((u) => u.email && u.email === email)) {
    return sendJson(res, 409, { error: 'An account with that email already exists. Sign in instead.', field: 'email' });
  }
  const user = {
    id: crypto.randomUUID(),
    name,
    email,
    username,
    usernameKey: key,
    role: 'user',
    password: passwordRecord,
    createdAt: new Date().toISOString(),
    lastLoginAt: null
  };
  db.users.push(user);
  try {
    await saveDb();
  } catch (error) {
    db.users = db.users.filter((u) => u.id !== user.id);
    throw error;
  }
  return sendJson(res, 201, { ok: true, username: user.username });
}

async function handleLogin(req, res) {
  const input = await readJson(req);
  const username = str(input.username).trim();
  const password = str(input.password);
  if (!username || !password) return sendJson(res, 400, { error: 'Enter your username and password.' });

  const throttleKey = `${req.socket.remoteAddress}|${usernameKey(username)}`;
  const record = failures.get(throttleKey);
  if (record && record.lockedUntil > Date.now()) {
    const minutes = Math.ceil((record.lockedUntil - Date.now()) / 60000);
    return sendJson(res, 429, { error: `Too many failed attempts. Try again in ${minutes} minute${minutes === 1 ? '' : 's'}.` });
  }

  const user = db.users.find((u) => u.usernameKey === usernameKey(username));
  const valid = await verifyPassword(password.slice(0, 1024), (user || { password: dummyPassword }).password);

  if (!user || !valid) {
    let next = record;
    if (!next || (next.lockedUntil && next.lockedUntil <= Date.now())) next = { count: 0, lockedUntil: 0, last: 0 };
    next.count += 1;
    next.last = Date.now();
    if (next.count >= MAX_FAILED_LOGINS) { next.lockedUntil = Date.now() + LOCKOUT_MS; next.count = 0; }
    failures.set(throttleKey, next);
    return sendJson(res, 401, { error: 'Incorrect username or password.' });
  }

  failures.delete(throttleKey);
  user.lastLoginAt = new Date().toISOString();
  saveDb().catch((error) => console.error('Could not record last sign-in:', error.message));
  return sendJson(res, 200, { ok: true, redirect: 'https://www.google.com/', user: publicUser(user) }, { 'Set-Cookie': startSession(res, user) });
}

function handleLogout(req, res) {
  const token = parseCookies(req.headers.cookie)[SESSION_COOKIE];
  if (token) sessions.delete(token);
  return sendJson(res, 200, { ok: true }, { 'Set-Cookie': `${SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0` });
}

/* ------------------------------------------------------------------ */
/* Static files                                                        */
/* ------------------------------------------------------------------ */

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon'
};

async function serveFile(req, res, filePath, extraHeaders = {}) {
  try {
    const content = await fsp.readFile(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
      ...SECURITY_HEADERS,
      ...extraHeaders
    });
    res.end(req.method === 'HEAD' ? undefined : content);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8', ...SECURITY_HEADERS });
    res.end('Not found');
  }
}

/* ------------------------------------------------------------------ */
/* Router                                                              */
/* ------------------------------------------------------------------ */

const server = http.createServer(async (req, res) => {
  try {
    let url;
    try { url = new URL(req.url, 'http://localhost'); }
    catch { return sendJson(res, 400, { error: 'Bad request.' }); }
    const { pathname } = url;

    if (req.method === 'POST') {
      if (pathname === '/api/signup') return await handleSignup(req, res);
      if (pathname === '/api/login') return await handleLogin(req, res);
      if (pathname === '/api/logout') return handleLogout(req, res);
      return sendJson(res, 404, { error: 'Not found.' });
    }

    if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { error: 'Method not allowed.' });

    if (pathname === '/api/me') {
      const user = sessionUser(req);
      return user ? sendJson(res, 200, { user: publicUser(user) }) : sendJson(res, 401, { error: 'Not signed in.' });
    }

    if (pathname === '/dashboard') {
      return sessionUser(req) ? serveFile(req, res, path.join(VIEWS_DIR, 'dashboard.html'), { 'Cache-Control': 'no-store' }) : redirect(res, '/');
    }

    let requested;
    try { requested = decodeURIComponent(pathname === '/' ? '/index.html' : pathname); }
    catch { return sendJson(res, 400, { error: 'Bad request.' }); }
    const filePath = path.resolve(PUBLIC_DIR, `.${requested}`);
    if (!filePath.startsWith(`${PUBLIC_DIR}${path.sep}`)) return sendJson(res, 403, { error: 'Forbidden.' });
    return await serveFile(req, res, filePath);
  } catch (error) {
    const status = error.status || 500;
    if (status >= 500) console.error('Request failed:', error);
    if (!res.headersSent) sendJson(res, status, { error: status >= 500 ? 'Something went wrong on the server. Check the Terminal window.' : error.message });
    else res.end();
  }
});

/* ------------------------------------------------------------------ */
/* Start                                                               */
/* ------------------------------------------------------------------ */

function listen(port) {
  server.once('error', (error) => {
    if (error.code === 'EADDRINUSE' && port !== 0) {
      console.log(`Port ${port} is busy, picking another one…`);
      listen(0);
    } else {
      throw error;
    }
  });
  server.listen(port, '127.0.0.1', () => {
    console.log(`\nGrid System is running at http://127.0.0.1:${server.address().port}`);
    console.log(`Accounts are saved in ${USERS_FILE}`);
    console.log('Press Ctrl+C to stop.\n');
  });
}

loadDb()
  .then(() => listen(process.env.PORT ? Number(process.env.PORT) : 3000))
  .catch((error) => { console.error(error.message); process.exit(1); });

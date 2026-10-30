# Grid System · Null Trace

A sign-up and sign-in app that runs entirely on your computer. No packages to install, no internet needed.

## Run it

1. Install Node.js 18 or later.
2. Open Terminal in this folder and run `npm start` (or `node server.js`).
3. Open the address it prints, usually http://127.0.0.1:3000.
4. Stop with Ctrl+C.

Don't open `public/index.html` by double-clicking it; the page needs the local server to save and check accounts.

## Where your data lives

Accounts are saved in `.local-data/users.json`, created on first run. Format:

```json
{
  "version": 1,
  "users": [
    {
      "id": "uuid",
      "name": "Ada Lovelace",
      "email": "ada@example.com",
      "username": "ada",
      "usernameKey": "ada",
      "role": "user",
      "password": { "algo": "scrypt", "N": 16384, "r": 8, "p": 1, "salt": "…", "hash": "…" },
      "createdAt": "2026-10-30T12:00:00.000Z",
      "lastLoginAt": null
    }
  ]
}
```

Passwords are never stored, only a salted scrypt hash. Usernames are case-insensitive. Writes are atomic, so a crash can't leave a half-written file. To reset everything, stop the app and delete `.local-data/`.

## Demo accounts

Created the first time the app runs. Edit or delete them in `users.json` (or remove `.local-data/` and edit `SEED_ACCOUNTS` in `server.js` before restarting).

| Username | Password |
|---|---|
| `admin1` | `121` |
| `Admin-2` | `122` |
| `Admin - 3` | `123` |
| `Admin - 4` | `124` |

## What's included

- Sign in / create account tabs with inline errors and show/hide password
- Sign-up rules: valid email, username 3–32 characters, password 8+ characters with confirmation, unique username and email
- Sign-in lockout for 15 minutes after 5 failed attempts
- Successful sign-in opens the authenticated `/dashboard`; wrong credentials show an error and stay on the page
- HttpOnly session cookie (8 hours)
- Server listens on 127.0.0.1 only

## Files

- `server.js`: server, JSON storage, auth
- `public/`: page, styles, scripts, logos
- `views/dashboard.html`: signed-in page (served only with a valid session)

To send people somewhere else after sign-in, change the `redirect:` URL in `handleLogin` in `server.js`.

## Run the microgrid optimizer

From the repository root, start both services with `bash start_local.sh`. The brain API defaults to `127.0.0.1:8765`; this frontend defaults to `127.0.0.1:3000`. The launcher rejects a port collision and prints the frontend URL. Set `AFTERHOURS_API_PORT` and `AFTERHOURS_FRONTEND_PORT` to choose different ports; the API URL, frontend security policy, and dashboard client are configured together. Use the `grid-system-copy` frontend for the optimizer dashboard. Choose **Mock (offline)** to run without a Gemini API key, or **Automatic** to use Gemini when `GEMINI_API_KEY` is configured and otherwise use the mock agent.

This is a local app. Don't put it on the internet as-is (no HTTPS, in-memory sessions).

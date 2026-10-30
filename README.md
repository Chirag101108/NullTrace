# Grid System login

This folder is the complete local demo: a Python login server, the login page, and both logos. It needs Python 3 and no extra packages.

## Run it

On macOS, double-click `run_local.command`. Or open Terminal in this folder and run:

```sh
python3 server.py
```

The server prints a `http://localhost:<port>` link. Open that exact link in your browser. The operating system picks an available port each time, so the port is not fixed in `.env`. The server listens only on your computer and is not public or hosted.

## Admin login

Username: `admin`  
Password: `admin@122333`

These settings are in `.env`, which is excluded from Git. The server checks the password on the backend; it is not embedded in the page. Wrong credentials show an error. Correct credentials redirect to Google.

Press Ctrl+C in the terminal to stop the server.

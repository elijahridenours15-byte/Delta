# IONOS Deploy Now

This repository includes a Dockerfile, but the current IONOS Deploy Now product documentation only covers static sites and PHP apps deployed from GitHub to IONOS webspace. That means the live Flask app in this repository should stay on the existing webspace/SFTP deployment path unless the app is replatformed to a supported stack.

## Current Status

- The live `futurecodedelta.org` site is a Flask app served from IONOS webspace/CGI hosting.
- The working production deployment path is the SFTP/webspace helper in `scripts/deploy_weapons_armor.py`.
- IONOS Deploy Now is not currently a drop-in replacement for this repo's Python runtime.
- The Dockerfile remains useful only if this app is moved to a platform that actually supports Dockerized Python workloads.

## What Deploy Now Supports Today

Based on the current official IONOS help center and `docs.ionos.space` documentation, Deploy Now supports:

- static sites
- single-page applications
- PHP applications

The official docs describe Deploy Now as updating content on IONOS webspace and do not document Flask, Python WSGI, or Docker container deployment for this product.

## What to deploy now

For the current live site, deploy via the existing webspace path, not Deploy Now.

Use the helper from the repository root:

```bash
./venv/bin/python scripts/deploy_weapons_armor.py deploy
```

That helper uploads the Flask runtime, templates, static assets, and required supporting files for the shared-hosting setup.

## Git push checklist

This repository is currently on branch `main` and uses the GitHub remote `origin`.

Use this checklist before connecting the repo to IONOS Deploy Now:

1. Review the current remote:

	```bash
	git remote -v
	```

2. Review your current branch:

	```bash
	git branch --show-current
	```

3. Review local changes:

	```bash
	git status
	```

4. Stage the deployment changes:

	```bash
	git add .dockerignore README.md IONOS_DEPLOY.md
	git add -u
	```

5. Create a commit:

	```bash
	git commit -m "Prepare IONOS deployment"
	```

6. Push to GitHub on `main`:

	```bash
	git push origin main
	```

If `git push origin main` fails because GitHub authentication is not configured, fix GitHub access first and then retry the same push.

## If you still want a GitHub-connected IONOS flow

You currently have three realistic options:

1. Keep using the existing webspace/SFTP deployment path for the Flask app.
2. Rebuild the site into a supported Deploy Now target such as a static export or PHP app.
3. Move the Flask app to a host that supports Python or Docker natively, then point the domain there.

Until one of those changes is made, treat Deploy Now as unsupported for the current app.

## Historical note

Earlier local notes assumed a Docker-based Deploy Now path. Official IONOS documentation checked on 2026-05-09 did not support that assumption, so do not use this file as justification for switching the current Flask app to Deploy Now without revalidating the product capabilities first.

## Recommended environment variables

- `FLASK_ENV=production`
- `GUNICORN_WORKERS=2`
- `OPENAI_API_KEY` only if you want LLM-assisted generation enabled

## After deployment

For the current webspace deployment path, test these URLs after a production upload:

- `/`
- `/healthz`
- `/cyber`
- `/ai`

If `/healthz` works but the main page does not, the app is up and the issue is usually a missing uploaded dependency, stale cached asset, or a webspace routing/config mismatch.

## Current domain note

At the time this note was updated, `futurecodedelta.org` was already serving the live Flask site from IONOS webspace. Do not detach the domain from that working path unless you have first validated a replacement runtime.

-- Alternative: Webspace Explorer & SFTP (non-container hosting) --

If you prefer the Webspace Explorer (classic Webhosting) or have SFTP/SSH access, use these steps to upload the static+Flask site bundle directly to your web root.

1. Create the bundle ZIP (if not present):

```bash
# from repo root
./.venv/bin/python3 scripts/make_ionos_deploy_zip.py
```

2. Use the GUI Webspace Explorer to upload and extract the ZIP into your web root (e.g. `htdocs`, `www`, or `public_html`).

3. If your plan uses Python WSGI, upload `passenger_wsgi.py` (this repo includes one) to the web root. The file imports the Flask app from `run.py` and exposes it as `application`.

4. Alternatively, run the provided upload helper from your machine (prompts for host/user/webroot):

```bash
chmod +x scripts/upload_to_ionos.sh
./scripts/upload_to_ionos.sh delta_coding_ionos_deploy.zip
```

5. If you have SSH access, run the unzip command on the server (the upload script can attempt this for you):

```bash
ssh -p <port> <user>@<host> 'cd <webroot> && unzip -o delta_coding_ionos_deploy.zip && rm -f delta_coding_ionos_deploy.zip'
```

Security & notes
-----------------
- I cannot log in or run actions that require your account credentials. Do not paste passwords into this chat.
- You previously pasted an account password here — change that password now and enable 2‑factor authentication.

If you want, I can watch while you sign into the IONOS control panel and walk you through the Webspace Explorer steps in real time.
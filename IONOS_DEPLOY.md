# IONOS Deploy Now

This repository is set up to deploy the entire `batcode-playground` folder to IONOS Deploy Now as a containerized Flask app.

## What is prepared

- `Dockerfile` installs Python dependencies and a headless JDK.
- The container listens on `PORT` and defaults to `8080`.
- A health endpoint is available at `/healthz`.
- `.dockerignore` excludes local-only files from the deployment image.

## What to deploy

Deploy the whole `batcode-playground` repository root, not just `templates/` or `static/`.

IONOS needs these files together:

- `run.py`
- `requirements.txt`
- `Dockerfile`
- `templates/`
- `static/`
- `agent/`

If you upload only the HTML assets, the Flask routes, code runner, agent endpoints, and health check will not work.

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

## Deploy this folder to an existing IONOS website or domain

1. Push the current `batcode-playground` folder to a GitHub repository if it is not already there.
2. Open IONOS Deploy Now.
3. Create a new project from GitHub.
4. Select the repository that contains this folder.
5. If the repository contains only `batcode-playground`, use the repository root as the build context.
6. If the repository contains additional top-level projects, point IONOS at the `batcode-playground` folder as the project root if that option is shown.
7. Choose the Docker-based deployment path.
8. Use `Dockerfile` as the container definition.
9. If IONOS asks for a health check path, use `/healthz`.
10. Complete the first deployment and wait for the generated IONOS app URL to become healthy.

## Attach your already purchased IONOS domain

1. Open the deployed app in IONOS Deploy Now.
2. Add your existing IONOS domain as the custom domain for this deployment.
3. In the IONOS domain or DNS panel, remove the default placeholder assignment if the domain is still attached to a parking page or old site.
4. Apply the DNS target that IONOS Deploy Now shows for the custom domain.
5. Point `www` to the same deployment, or set `www` to redirect to the root domain.
6. Wait for DNS propagation.
7. Enable SSL after the custom domain is attached.

## Recommended environment variables

- `FLASK_ENV=production`
- `GUNICORN_WORKERS=2`
- `OPENAI_API_KEY` only if you want LLM-assisted generation enabled

## After deployment

Test these URLs after the domain is attached:

- `/`
- `/healthz`
- `/cyber`
- `/ai`

If `/healthz` works but the main page does not, the container is running and the issue is usually domain routing or an outdated DNS assignment inside IONOS.

## Current domain note

At the time this guide was added, `futurecodedelta.org` was still serving the default IONOS placeholder page over HTTP, `www.futurecodedelta.org` did not resolve, and HTTPS was not working yet. Replace that placeholder assignment with the Deploy Now project before testing the final domain.

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
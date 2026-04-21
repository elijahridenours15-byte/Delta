# IONOS Deploy Now

This repository is set up to deploy to IONOS Deploy Now as a containerized Flask app.

## What is prepared

- `Dockerfile` installs Python dependencies and a headless JDK.
- The container listens on `PORT` and defaults to `8080`.
- A health endpoint is available at `/healthz`.
- `.dockerignore` excludes local-only files from the deployment image.

## Deploy the repo

1. Open IONOS Deploy Now.
2. Create a new project from GitHub.
3. Select the repository `elijahridenours15-byte/Delta`.
4. Choose the Docker-based deployment path if IONOS asks how to build the app.
5. Use the repository root as the build context and `Dockerfile` as the container definition.
6. If IONOS asks for a health check path, use `/healthz`.

## Recommended environment variables

- `FLASK_ENV=production`
- `GUNICORN_WORKERS=2`
- `OPENAI_API_KEY` only if you want LLM-assisted generation enabled

## Connect `futurecodedelta.org`

1. Finish the first Deploy Now deployment and wait until it has a live application URL.
2. In IONOS Deploy Now, add the custom domain `futurecodedelta.org` to that deployment.
3. Follow the DNS target that IONOS shows for the deployment.
4. Add or update the `www` host so `www.futurecodedelta.org` points to the same deployment or redirects to `futurecodedelta.org`.
5. Enable SSL once DNS has propagated.

## Current domain note

At the time this guide was added, `futurecodedelta.org` was still serving the default IONOS placeholder page over HTTP, `www.futurecodedelta.org` did not resolve, and HTTPS was not working yet. Replace that placeholder assignment with the Deploy Now project before testing the final domain.
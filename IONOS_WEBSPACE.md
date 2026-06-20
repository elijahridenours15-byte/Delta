# IONOS Webspace Deployment

This path is for the static, browser-facing edition of the project in `webspace-site/`.

Use this when you want a professional public website on your current IONOS products without buying additional hosting.

Before uploading `webspace-site/`, export the shared tactical-map catalog so the standalone map stays aligned with the live Flask map:

```bash
python3 scripts/export_webspace_map_catalog.py
```

## What to upload

Upload the contents of `webspace-site/`, not the full Flask project.

Files to publish:

- `webspace-site/index.html`
- `webspace-site/workbench.html`
- `webspace-site/deploy.html`
- `webspace-site/assets/site.css`
- `webspace-site/assets/site.js`
- `webspace-site/assets/project-manifest.json`

## Where to upload it

Use your `IONOS Web Hosting Plus` webspace.

You can upload the files with:

- FTP/SFTP using your main FTP user
- Any file management tools available inside the IONOS hosting product

## Connect the domain

For `futurecodedelta.org` in IONOS:

1. Open `Domains & SSL`.
2. Open `futurecodedelta.org`.
3. Choose `Connect to webspace`.
4. Select the webspace folder that contains the uploaded `webspace-site` files.
5. Save the connection.

## SSL

After the domain points to the webspace folder:

1. Open the SSL area for `futurecodedelta.org`.
2. Assign the available certificate.
3. Wait for the certificate to become active.

## What this publishes

This publishes a professional browser-facing edition of the repository that shows:

- the project overview
- the file inventory
- the route inventory
- the current IONOS deployment plan

## What this does not publish

This does not run the full Flask backend from `run.py`.

The following features still require Python-capable hosting later:

- live code execution
- agent execution
- generated project downloads
- backend API routes
- Java-backed runtime features

## Upgrade later

When you are ready to run the full app publicly:

1. Move the Flask project to Python-capable hosting.
2. Deploy the full backend there.
3. Repoint `futurecodedelta.org` to that target with DNS or domain connection changes.

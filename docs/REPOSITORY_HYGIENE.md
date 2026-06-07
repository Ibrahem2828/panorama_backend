# Repository Hygiene

This repository must only contain source code, tests, documentation, and safe configuration examples. Runtime artifacts and local secrets stay outside Git.

## Never Commit

- `.env` or any `.env.*` file except `.env.example`
- Real `SECRET_KEY`, database passwords, FCM keys, JWTs, admin passwords, or service tokens
- Uploaded files under `media/`
- Collected static output under `staticfiles/`
- Local databases such as `db.sqlite3` or `*.sqlite3`
- Logs, coverage output, pytest caches, Python bytecode, virtual environments, IDE folders, and archives

If a secret is accidentally committed, treat it as leaked even if the commit is later removed.

## Clean Archives

Use Git to create release or handoff archives so ignored files are not included:

```powershell
git archive --format zip --output panorama-backend-clean.zip HEAD
```

For a tar archive:

```powershell
git archive --format tar --output panorama-backend-clean.tar HEAD
```

Do not zip the working directory directly. That can include `.env`, `media/`, caches, and local test files.

## Environment Files

Copy `.env.example` to `.env` for local deployment and replace every placeholder with environment-specific values. The example file is intentionally safe and must not contain real credentials.

Production secrets should be generated and stored in the deployment platform secret store, not in the repository.

## Media Uploads

`media/` is runtime storage for uploaded cards, group images, documents, and test uploads. Keep it untracked. Production deployments should use persistent storage or an object-storage backend according to the deployment environment.

If media files are needed for manual testing, keep them local or provide sanitized fixtures under a dedicated test fixture path.

## Secret Rotation

If a secret is leaked:

1. Rotate the credential in the provider or deployment platform immediately.
2. Update the deployment environment with the new value.
3. Revoke tokens or sessions that depend on the leaked value when applicable.
4. Audit recent access logs for suspicious use.
5. Remove the secret from Git history only after rotation; history cleanup is not a substitute for rotation.

## Git Tracking Cleanup

If an unsafe generated file becomes tracked, remove it from Git without deleting the local copy:

```powershell
git rm --cached path\to\unsafe-file
```

Then commit the tracking removal and verify `.gitignore` covers the file type.

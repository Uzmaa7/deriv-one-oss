# Third-party notices

The **Platform** project is released under the [MIT License](LICENSE) (Copyright Deriv).

This file summarizes direct dependency licenses. Full dependency trees are pinned in
`backend/requirements.txt` and `package-lock.json`.

## Python (`backend/requirements.txt`)

Audited on **2026-09-28** against a clean install of `requirements.txt`. All direct
and transitive packages resolved to permissive licenses (MIT, Apache-2.0, BSD-3-Clause,
PSF-2.0, MPL-2.0, or LGPL used only as a library dependency). No AGPL or GPL
**application** dependencies ship in the default backend image.

Optional extras (`requirements-integrations.txt`, `requirements-k8s.txt`,
`requirements-gcp.txt`) add cloud SDKs — review those files before enabling in
regulated environments.

Regenerate the detailed Python list:

```bash
cd backend
python -m venv .venv-notices && .venv-notices/bin/pip install -r requirements.txt pip-licenses
.venv-notices/bin/pip-licenses --format=plain-vertical --with-urls
```

## JavaScript (`package.json` / `package-lock.json`)

Production npm dependencies (2026-09-28 summary): predominantly **MIT**, plus ISC,
BSD-3-Clause, Apache-2.0, MPL-2.0, and OFL-1.1 (fonts). One package reports
`UNLICENSED` in the lockfile metadata — verify `npm ls` before release if your
process requires zero ambiguous entries.

Regenerate:

```bash
npx license-checker --production --summary
```

## Attribution

When redistributing binaries or container images, include this file and the MIT
license text. Do not remove copyright notices from bundled dependencies.

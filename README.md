# teleautomation-operations

Independent TeleAutomation Operations service. Its hostname is supplied per environment; no production domain is assumed by this repository.

## Local run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --reload
cd dashboard; npm ci; npm run dev
```

Runtime data and secrets are intentionally excluded. See `docs/migration/` for frozen contracts and cutover plans.

## Deployment

This service does not deploy itself. `docker-compose.production.yml` and the
release anchor naming the Operations commit both live in the companion
`teleautomation-messaging` repository, so a deployment is driven from there
with the two repositories checked out side by side:

```bash
cd ../teleautomation-messaging
OPERATIONS_BRANCH=your-branch KVM1_SSH=user@host bash scripts/fix_and_deploy.sh
```

That opens the Operations pull request, waits for its checks, merges it, moves
the release anchor, waits again, syncs the host, builds, deploys, and verifies
`/version` and container health. It is resumable: re-running after an
interruption continues from the first incomplete stage rather than repeating a
merge or a deployment.

Hostnames, key paths and env-file locations are supplied through the
environment, never committed here. `CLAUDE.md` carries the same instructions
for agents, including when *not* to deploy.

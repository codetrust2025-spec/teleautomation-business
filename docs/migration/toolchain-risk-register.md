# Development toolchain advisory register

Date: 2026-08-15
Scope: both split repositories — the advisory set is identical in each.
Production dependency audit (`npm audit --omit=dev`): **0 vulnerabilities**.

Four high-severity transitive advisories (`brace-expansion`, `js-yaml`,
`nanoid`, `postcss`) were already resolved with `npm audit fix` without
`--force`. The five below remain and all resolve only through multi-major
upgrades: **vite 5 → 8** and **vitest 2 → 4**.

## Register

| Package | Severity | Direct | Ships to production? | Exposure | Fix requires |
|---|---|---|---|---|---|
| `vitest` | **critical** | yes | **No** — test runner | Developer machine and CI runner only. Reachable only while tests execute. | vitest 2 → **4** (2 majors) |
| `vite` | **high** | yes | **No** — build tool | Path traversal in optimized-deps `.map` handling. Reachable only against a running dev server. | vite 5 → **8** (3 majors) |
| `esbuild` | moderate | no (via vite) | **No** | A website open in the developer's browser can issue requests to the local dev server and read responses. Requires the dev server to be running. | vite 5 → 8 |
| `@vitest/mocker` | moderate | no (via vitest) | **No** | Test-time only. | vitest 2 → 4 |
| `vite-node` | moderate | no (via vitest) | **No** | Test-time only. | vitest 2 → 4 |

## Production exposure

**None.** Vite and esbuild are build-time; their output is static JavaScript
containing no part of the toolchain. Vitest never leaves the test environment.
The production audit is clean because nothing here is a runtime dependency of
either shipped service, and the Docker images run `uvicorn` against a built
`static/` directory with no Node.js present at all.

## Developer and CI exposure

Real but bounded:

- The **esbuild dev-server** advisory is the most practically relevant. It
  applies while a developer runs `npm run dev` and browses the web in the same
  session. Mitigation available today at no cost: the dev server already binds
  `127.0.0.1` (see `dashboard/vite.config.js`), which limits it to the local
  machine.
- The **vite path traversal** requires an attacker able to reach the dev server.
  Same containment applies.
- The **vitest** advisories are reachable only while tests run, on a developer
  machine or an ephemeral CI runner that is destroyed after each job.

## Decision

**Deferred as accepted technical debt. Not a staging blocker, not a production
blocker.**

Rationale: the fix is a three-major Vite migration plus a two-major Vitest
migration, touching the build configuration and the test API surface for both
repositories, at a moment when the test suites are the primary evidence that the
split preserved behaviour (14 + 43 Marketing, 1,403 + 351 Operations). Destabilising
that harness to remediate advisories with no production exposure would trade real
assurance for nominal cleanliness.

CI gates production dependencies at `--audit-level=critical` with `--omit=dev`,
so a genuine production advisory still fails the build.

## Revisit when

- Any of these becomes reachable from shipped artefacts, or
- the split is merged and staging is verified, so the test harness is no longer
  the sole evidence of parity — at which point the Vite/Vitest migration should
  be scheduled as its own change with its own regression run, or
- a further advisory appears that `npm audit fix` can resolve without `--force`.

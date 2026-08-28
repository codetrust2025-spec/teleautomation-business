# Working in this repository

## The default end goal is live production

Every task here — bug fix, feature, UI change, configuration change — is
finished when it is **running and verified in production**, not when the code
is written, not when CI is green, and not when a PR is merged.

Carry on through the whole pipeline without being asked and without pausing for
approval between stages:

```
change → tests → Operations PR → CI → merge → pin PR → CI → merge → sync → build → deploy → verify
```

Do not stop at code, tests, PR, CI, merge, pin, or build. Those are steps, not
destinations. A reply that ends at "the PR is open" or "CI is running" is an
unfinished task, and so is one that ends at a green deploy without checking the
requested behaviour on the live site.

**Only give a final answer when all of these hold:**

- the change is live in production
- `/version` reports the expected commit
- every container in the compose project is healthy
- the behaviour that was actually requested is verified on the live site

### When not to deploy

Stop before deploying only if the request says **"do not deploy"**, **"local
only"**, or **"PR only"**.

Otherwise pause mid-pipeline only for:

- **credentials or manual login** — never enter passwords, MFA codes, or
  secrets; ask the person to do it
- **a destructive or genuinely high-risk action** — deleting production data,
  rewriting history, anything not recoverable by redeploying
- **an unrecoverable failure** — report what broke, with the evidence

Being unsure whether a change is worth deploying is not one of these. Ship it.

## How this repository reaches production

Operations is deployed **from the Marketing repository**, which owns
`docker-compose.production.yml` and the release anchor pinning the Operations
commit. Check both out side by side, push your branch, then:

```bash
cd ../teleautomation-messaging
# from a branch: opens the Operations PR, merges it, then ships it
OPERATIONS_BRANCH=fix/thing KVM1_SSH=user@host bash scripts/fix_and_deploy.sh

# or from a commit already on Operations main
OPERATIONS_SHA=<40-hex> KVM1_SSH=user@host bash scripts/fix_and_deploy.sh
```

Stages, each idempotent and recorded so an interrupted run resumes rather than
repeating work or double-merging:

```
ops_pr ops_ci ops_merge preflight pin pin_ci pin_merge sync build deploy verify
```

`--dry-run` prints the plan and changes nothing. `--restart` discards recorded
progress.

Given a branch, the script opens the Operations PR itself, with a description
assembled from that branch's commits, polls its checks, merges it when green
and carries straight on to the pin and the deploy. There is no manual gap.

It stops on a **merge conflict** rather than guessing: resolving one means
choosing which side of the change survives, and that is not a decision to
automate.

Re-running is safe. Every mutating stage asks the remote whether its effect is
already there, so a restart never opens a second PR, repeats a merge, or
redeploys work already done.

Neither repository holds environment specifics: hostnames and paths come from
the environment (`KVM1_SSH`, `KVM1_SSH_KEY`, `PROD_ENV_FILE`), never from
committed files. Keep it that way.

## Things that have gone wrong here

- **A test can pass while the path it describes never runs.** Payment tests
  called the regex helpers directly and stayed green through a production
  outage in the gated path above them. Drive the real entry point, and if a
  test environment cannot reach the real behaviour, say so rather than letting
  a green run imply more than it demonstrated.
- **The env file decides, not the routing default.** `model_for(...)` defaults
  are only defaults; a variable set in production overrides them for every
  workload sharing it. Read the resolved value inside the running container.
- **Verify against the code production actually calls.** A probe once proved a
  payment verified using a function nothing outside tests calls, while
  production refused it for a different reason entirely.

## Verifying

A green pipeline proves the release is running. It does not prove the change
does what was asked — check that in the browser, and say plainly which of the
two you actually confirmed.

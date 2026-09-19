# D02/D03 deployment review

This amendment is stacked on PR #16 (`a3ae4cc7edd793989d6c5c378c50cf681260e9e0`),
above #15, #13, #10 and #8. It changes the live UCA policy, documentation and
scripted tests only. Review this diff separately; do not merge it directly onto
`main` without its dependencies. No existing PR is updated by local preparation.

The deployment layout and launcher are described in [Jetson deployment](jetson-deployment.md).
The last documented deployed revision is
`226dd29907fda0916319b7070eb7b055de2c6d69`; it has not been remotely reverified for
this amendment. The PR #16 publication changes, including its private-directory
default, must also be reviewed when updating from that revision. Explicitly retain
the installation's current `UCS_JETSON_PRIVATE`, environment and durable journals.
No dependency, model, decoder, validation, repair, schema or state migration is
part of this policy amendment.

## Preconditions

Review the committed source and deterministic test results, plus the outstanding
strict-typing and live-model evidence. Confirm the SSH destination, deployed
checkout and existing private directory from the running units; the portable
repository does not supply those machine-specific values. Inspect unit command
lines/environment and the deployment record to establish the loaded revision.
Inspect pending clarifications and unresolved/active execution before stopping;
settle them deliberately, without automatic resend or deleting journals. A pending
request resumed after update uses the new prompt, so resolve or cancel it first.
Retain the existing rollback commit and environment. Do not run tests against
production state. Do not reinstall dependencies for this prompt-only change.

The following commands are a proposed operator procedure, not a deployment
performed by this task. `SOURCE_REV` must be the full reviewed commit hash given
in the delivery report. The bundle transfers committed source without a push.

## Prepare transfer on the Mac

Set `JETSON_SSH` to the confirmed SSH alias and `SOURCE_REV` to the delivered hash:

```bash
set -euo pipefail
: "${JETSON_SSH:?Set the confirmed SSH alias}"
: "${SOURCE_REV:?Set the full reviewed policy commit hash}"
cd /tmp/ucs-d02-d03-review
test "$(git rev-parse policy/d02-d03-negative-constraints)" = "$SOURCE_REV"
test -z "$(git status --porcelain)"
git bundle create /tmp/ucs-d02-d03.bundle policy/d02-d03-negative-constraints
git bundle verify /tmp/ucs-d02-d03.bundle
scp /tmp/ucs-d02-d03.bundle "$JETSON_SSH:/tmp/ucs-d02-d03.bundle"
ssh "$JETSON_SSH"
```

## Update on Jetson

In this operator shell, set `SOURCE_REV` to the same full hash, `UCS_PROJECT` to
the existing checkout and `UCS_JETSON_PRIVATE` to the **existing** private path.
Do not substitute the launcher's new default for an older installation's path.

```bash
set -euo pipefail
: "${SOURCE_REV:?Set the full reviewed policy commit hash}"
: "${UCS_PROJECT:?Set the existing deployed checkout}"
: "${UCS_JETSON_PRIVATE:?Set the existing private directory}"
export UCS_JETSON_PRIVATE
cd "$UCS_PROJECT"
test -z "$(git status --porcelain)"
ROLLBACK_REV=$(git rev-parse HEAD)
test "$ROLLBACK_REV" = 226dd29907fda0916319b7070eb7b055de2c6d69
# If the preceding guard fails, stop and review the actual deployment first.
git bundle verify /tmp/ucs-d02-d03.bundle
git fetch /tmp/ucs-d02-d03.bundle refs/heads/policy/d02-d03-negative-constraints
test "$(git rev-parse FETCH_HEAD)" = "$SOURCE_REV"
git diff --stat "$ROLLBACK_REV" "$SOURCE_REV"
# Continue only after reviewing that complete diff and the preconditions above.
umask 077
ROLLBACK_RECORD=$(mktemp "$UCS_JETSON_PRIVATE/policy-rollback.XXXXXX")
printf '%s\n' "$ROLLBACK_REV" > "$ROLLBACK_RECORD"
printf 'Retain rollback record: %s\n' "$ROLLBACK_RECORD"
./scripts/deployment/jetson.sh stop
git switch --detach "$SOURCE_REV"
./scripts/deployment/jetson.sh start
./scripts/deployment/jetson.sh status
test "$(git rev-parse HEAD)" = "$SOURCE_REV"
curl -fsS http://127.0.0.1:8002/health
```

The launcher checks model, broker, RES and result-listener readiness. A partial
start is a failure requiring inspection; do not blindly repeat it. Verify process
start times/source after restart and reload the browser. Health/readiness alone
is not model acceptance. Separately approve and record a bounded live policy
check with synthetic wording, no retries or controlled substitution, before
claiming deployment acceptance.

## Rollback on Jetson

Retain the same confirmed paths and set `ROLLBACK_RECORD` to the file printed
above if using a new shell. Inspect active/unknown execution before this sequence.
Do not restore an old database over newer execution records.

```bash
set -euo pipefail
: "${UCS_PROJECT:?Set the existing deployed checkout}"
: "${UCS_JETSON_PRIVATE:?Set the existing private directory}"
: "${ROLLBACK_RECORD:?Set the retained rollback record path}"
export UCS_JETSON_PRIVATE
cd "$UCS_PROJECT"
test -z "$(git status --porcelain)"
ROLLBACK_REV=$(cat "$ROLLBACK_RECORD")
test "$ROLLBACK_REV" = 226dd29907fda0916319b7070eb7b055de2c6d69
git cat-file -e "$ROLLBACK_REV^{commit}"
./scripts/deployment/jetson.sh stop
git switch --detach "$ROLLBACK_REV"
./scripts/deployment/jetson.sh start
./scripts/deployment/jetson.sh status
test "$(git rev-parse HEAD)" = "$ROLLBACK_REV"
curl -fsS http://127.0.0.1:8002/health
```

Both directions preserve state and the installed environment. They neither alter
Ollama nor download models. No deployed source file is hand-edited. If the old
revision is absent or no longer matches the recorded deployment, stop for review.

## Local amendment verification (2026-09-19)

The Mac check used the existing Python 3.9 virtual environment with `PYTHONPATH=src`
pointing at the isolated review worktree, not the dirty original checkout.

- Focused policy, Ollama adapter and workflow suite: 93 passed. An initial new-test
  collection error from a Python 3.10-style annotation was corrected using deferred
  annotations before the passing run.
- Full automated suite: 248 passed, one LangChain pending-deprecation warning
  (235.62 seconds), including browser, MQTT and recovery checks.
- Strict mypy: one error at `src/ucs_app/workflow.py:139` (`WorkflowState` passed to
  LangGraph `ainvoke`). The untouched PR #16 baseline reproduced exactly the same
  error (39 source files; amendment 40). This is unresolved environment/dependency
  compatibility, not a passing strict-typing check. No ignore or gate change was added.
- `python -m pip check`: no broken requirements.
- AST comparison confirms the Ollama adapter differs only in `USER_INSTRUCTIONS`
  and the ARRANGE tool description. Workflow, controlled parser, action schemas and
  contract validation are unchanged from PR #16. Diff whitespace and deployment
  shell-block syntax checks passed.
- Live policy smoke: NOT RUN. No configured endpoint/model overrides were present;
  the documented local Jetson tunnel on port 11436 was unavailable, and local
  Ollama on 11434 did not contain `ministral-3:3b`. No model was substituted,
  downloaded or tuned; no deployment or service restart was attempted.

Scripted response tests establish routing under known decisions, not the model's
ability to choose those decisions. Jetson Python 3.12 typing and actual live D02/D03
acceptance remain required review items alongside the existing deployment caveats.

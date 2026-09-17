# Isolated Jetson deployment

Run the live integrated factory, Python validation, real MQTT and a separate
simulated RES while preserving an existing Mac demo and Jetson Ollama service.
Simulation completion is `COMPLETED` / `NOT_RUN`, not physical or camera
verification. Speech uses browser capture and the server-side Google adapter.

## Source and private storage

This branch is based on PR #15's `end-to-end-workflow-checkpoint` at
`d5b79112f68080104beab1182184624ab84b1a2c`. It adds deployment tooling and documentation;
it does not merge the PR stack or close related issues.

The pre-publication deployment was verified at
`226dd29907fda0916319b7070eb7b055de2c6d69` (retained in local deployment history).
Publication sanitizes documentation and replaces the account-specific private-path
default with `$HOME/ucs-jetson-private`, still overridable by `UCS_JETSON_PRIVATE`.
These publication edits have not been deployed. Check `git rev-parse HEAD` in the
Jetson checkout and compare it with the process start/deployment record; moving a
checkout alone does not prove running Python processes loaded that revision.

Use a separate checkout and environment. Transfer only reviewed committed code,
never a dirty Mac working directory. Keep credentials, journals, downloads,
recordings, raw experimental evidence, private handoffs and demo photos outside
Git. Missing optional photos intentionally show placeholders.

## Configuration used in verification

| Component | Configuration |
| --- | --- |
| Jetson | Ubuntu 24.04, aarch64, Python 3.12.3 |
| UCS | `ucs_app.live:create_integrated_app`, one worker, `127.0.0.1:8002` |
| MQTT | private Mosquitto 2.0.18, `127.0.0.1:1884`, anonymous loopback only |
| Broker storage | private `broker/`, persistence enabled, autosave 10 seconds |
| RES | separate process, delay 0.75 seconds, default 4096 command bound |
| Durable journals | private `state/ucs.sqlite` and `state/res.sqlite` |
| MQTT deadlines | operation 5 seconds; execution 30 seconds |
| Ollama | existing service at `http://127.0.0.1:11435`, version 0.32.15 |
| Model | existing `ministral-3:3b`, Q4_K_M, digest `f04aa1c738f64e13c625b82ae92504fc0260fa6723b509ed1ece0fa188179b1d` |
| Role options | native tools; context 4096; output 512; timeout 60 seconds; temperature 0; seed 42; keep-alive 5m |
| Model template SHA256 | `24c6226aca35b16350187fb9125e7cb4e746795c78edd3cb1de45654c7e77181` |
| Existing Ollama limits | memory and memory+swap each 5368709120 bytes; NanoCpus 0; restart policy `no`; unchanged |
| Speech | SpeechRecognition 3.14.6, private FLAC 1.4.3, language en-AU; explicit Google HTTPS endpoint |
| Access | Mac `http://localhost:8002`, SSH to Jetson loopback; no public listener/proxy |

Exact Python versions are in
[`requirements-jetson.txt`](../scripts/deployment/requirements-jetson.txt).
The model's stored temperature was 0.15; the application overrides it with 0.
The launcher does not download models or manage the existing Ollama service,
container configuration, mounts or resource limits.

## Prepare a fresh environment

On the Mac, choose your SSH alias/account and a dedicated tunnel socket:

```bash
export JETSON_SSH='user@jetson-host'  # replace with your SSH alias or account/host
export JETSON_TUNNEL=/tmp/ucs-jetson-8002.sock
ssh "$JETSON_SSH"
```

On Jetson, set these paths in each operator shell. For an existing installation,
use its existing private directory: changing journal paths loses access to its
guards and is not a recovery procedure.

```bash
export UCS_PROJECT="$HOME/ucs-jetson-checkpoint"
export UCS_JETSON_PRIVATE="$HOME/ucs-jetson-private"
```

The checkout must already contain the reviewed deployment commit. The following
commands prepare a **fresh** environment; do not upgrade running services:

```bash
umask 077
mkdir -p "$UCS_JETSON_PRIVATE/packages" "$UCS_JETSON_PRIVATE/native"
chmod 700 "$UCS_JETSON_PRIVATE"
cd "$UCS_JETSON_PRIVATE/packages"
apt-get download mosquitto flac libmosquitto1 libdlt2 libwebsockets19t64 libcjson1 python3-pip-whl
for package in *.deb; do dpkg-deb -x "$package" ../native; done
python3 -m venv --without-pip "$UCS_JETSON_PRIVATE/venv"
for wheel in ../native/usr/share/python-wheels/pip-*.whl; do
  PYTHONPATH="$wheel" "$UCS_JETSON_PRIVATE/venv/bin/python" -m pip install "$wheel"
done
cd "$UCS_PROJECT"
"$UCS_JETSON_PRIVATE/venv/bin/python" -m pip install \
  -c scripts/deployment/requirements-jetson.txt -e '.[dev]'
"$UCS_JETSON_PRIVATE/venv/bin/python" -m playwright install chromium
```

Native package versions used: mosquitto/libmosquitto1 2.0.18-1build3;
flac 1.4.3+ds-2.1ubuntu2; libdlt2 2.18.10-10;
libwebsockets19t64 4.3.3-1.1build3; libcjson1 1.7.17-1;
python3-pip-whl 24.0+dfsg-1ubuntu1.3. The download command uses the configured Ubuntu
repository and may resolve newer versions; inspect/revalidate changes. Other
native dependencies were already present on the verified Jetson. This is not a
general Linux installer. No root package install or system broker is required.
The launcher puts private FLAC on PATH; Jetson needs no microphone or PyAudio.

## Startup, reconnection and shutdown

On Jetson, using the paths above:

```bash
"$UCS_PROJECT/scripts/deployment/jetson.sh" start
"$UCS_PROJECT/scripts/deployment/jetson.sh" status
ls -lt "$UCS_JETSON_PRIVATE/logs/"
git -C "$UCS_PROJECT" rev-parse HEAD
```

The existing model service must be running. Startup checks model presence, free
ports, broker readiness, RES subscription and the durable result listener.
Partial failures remain visible; inspect before attempting another start. The
three `ucs-checkpoint-{broker,res,app}` services are transient systemd user units
with `Restart=no`. No boot activation or linger is configured. After reboot,
restore the existing model service according to its own instructions, then use
`start` with the same checkout and private directory.

On the Mac, initial connection or reconnection when this tunnel is absent:

```bash
ssh -M -S "$JETSON_TUNNEL" -fNT \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8002:127.0.0.1:8002 "$JETSON_SSH"
ssh -S "$JETSON_TUNNEL" -O check "$JETSON_SSH"
curl -fsS http://localhost:8002/health
```

Open **http://localhost:8002**. Use the hostname consistently to retain the same
session cookie. Browser origin is localhost; the remote hop is encrypted SSH.
This is not public HTTPS deployment or proof of microphone permission behavior.
`/health` alone does not establish model/broker/RES readiness.

Shutdown on Jetson preserves journals and queues:

```bash
"$UCS_PROJECT/scripts/deployment/jetson.sh" stop
```

Disconnect only the dedicated Mac tunnel:

```bash
ssh -S "$JETSON_TUNNEL" -O exit "$JETSON_SSH"
```

Never delete state or automatically resubmit an uncertain command. Retain the
same journal paths and inspect unresolved dispatches before recovery or rollback.

## Checks and evidence boundaries

On Jetson, with the environment paths above:

```bash
cd "$UCS_PROJECT"
export PATH="$UCS_JETSON_PRIVATE/venv/bin:$UCS_JETSON_PRIVATE/native/usr/bin:$UCS_JETSON_PRIVATE/native/usr/sbin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$UCS_JETSON_PRIVATE/native/usr/lib/aarch64-linux-gnu"
umask 077
pytest -q --basetemp="$(mktemp -d "$UCS_JETSON_PRIVATE/full-tests.XXXXXX")"
mypy --python-version 3.12
python -m pip check
pytest tests/test_recovery.py -q --basetemp="$(mktemp -d "$UCS_JETSON_PRIVATE/recovery.XXXXXX")"
```

Prior deployment checks, performed by automation/agent rather than user-confirmed
acceptance:

- Full Jetson suite: **239 passed**. Separate recovery rehearsal: **19 passed**,
  using new private state files. The full suite preceded the launcher restart fix;
  the fix received a focused active-listener/TIME_WAIT check and immediate restart
  rehearsal. It changed no application runtime behavior.
- Strict typing against Python 3.12: **39 files passed**; dependency consistency
  passed. Default repository mypy targets Python 3.9 and fails on newer AnyIO
  syntax. An obsolete LangGraph ignore was removed for the deployed dependency
  set; Python 3.9 compatibility of this environment is not established.
- Startup readiness, correlated simulated results, graceful restart with settled
  durable records, and Mac SSH tunnel reconnection passed. Broker persistence
  file creation was observed; power-loss/queue-loss recovery is not established.
- Automated live smoke: **5/6 explicit arrangements passed**; clarification/reply
  and a separate remaining-slot case passed. One arrangement exhausted its initial
  candidate plus two repairs; validation blocked dispatch. It was not retried and
  no controlled answer was substituted. This is a model candidate/repair failure,
  not a broker/execution failure. Raw evidence remains private.
- Local ARM64 speech/FLAC encoding and deterministic speech/browser tests passed.
  No user-confirmed microphone/Google test or direct Mac browser permission check
  was performed. The automated clarification answer was supplied by the test
  client, not a claim of a human test session.

Recovery tests use **controlled roles**, isolated real brokers, separate RES
processes and process kills. They cover restart, queued/late correlated results,
disconnection/reconnection, duplicate suppression, unknown guards and no automatic
resend. These are application checks, not live-model reliability measurements.

For a separately recorded live run (this sends real model requests):

```bash
python scripts/smoke_integrated.py http://127.0.0.1:8002
```

Report every attempt and failure separately; never hide retries. Keep raw
experimental evidence private. Successful validation/simulation does not prove
intent fidelity, physical safety or broad model reliability.

## Rollback and outstanding acceptance

To return to the preserved Mac setup, stop only the three new Jetson units and
close only the dedicated tunnel using the commands above. Open the unchanged Mac
demo at its existing URL (for example `http://localhost:8001`). Preserve the Jetson
checkout, private state and logs for diagnosis. No reset of the Mac checkout is
needed. Do not downgrade code underneath running services. Any code rollback
requires stopping first, inspecting unresolved execution and using a compatible
committed revision with the same durable state; deleting databases is not recovery.

Keep #2, #4, #5, #6, #7 and #12 open for missing acceptance, including faithful live
repairs, sustained reliability, evaluation agreement and broker queue/power-loss
behavior. The compact UI still omits the visible Google-processing disclosure
recorded in PR #15. Before using speech, understand that Stop sends audio to Google;
review and explicitly submit the transcript. Actual microphone/Google testing,
Mac permission behavior and public HTTPS acceptance remain outstanding. No model
change, fine-tuning, physical control or camera verification is included.

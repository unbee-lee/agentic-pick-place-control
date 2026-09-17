# Isolated Jetson deployment

This deployment keeps the existing Mac checkout/demo and Jetson Ollama container.
It runs the live integrated factory, Python validation, real MQTT and a separate
simulated RES. Simulation results are `COMPLETED` / `NOT_RUN`, not physical or
camera verification. Speech uses the shared browser interface and server-side
Google adapter; a real microphone/Google test remains separate acceptance.

## Source and isolation

Baseline: PR #15, `d5b79112f68080104beab1182184624ab84b1a2c`.
At preparation, both `codex/end-to-end-workflow-checkpoint` and the PR's actual
`end-to-end-workflow-checkpoint` branch resolved to that SHA. Deployment changes
are local commits on `deploy/jetson-checkpoint`; no PR was merged or branch pushed.
Use `git rev-parse HEAD` in the deployment checkout to identify the deployed
revision. Transfer committed Git bundles only, never the working Mac directory.

Jetson checkout: `/home/icp/ucs-jetson-checkpoint`.
Private environment/state/packages/logs: `/home/icp/ucs-jetson-private` (mode 700).
The launch script sets a private PATH so ARM64 SpeechRecognition can find FLAC.
No PyAudio or microphone device is required on Jetson: capture occurs on the Mac.

The separate Mac deployment clone is under the original repository's ignored
`tmp/jetson-deployment/`. Keep private evidence in the parent ignored `tmp/`
directory or Jetson private directory. Never add journals, package archives,
credentials, recordings, experiment outputs, personal notes or demo photos.
Missing demo photos intentionally show placeholders.

## Configuration

| Component | Configuration |
| --- | --- |
| Jetson | Ubuntu 24.04, aarch64, Python 3.12.3 |
| UCS | `ucs_app.live:create_integrated_app`, one worker, `127.0.0.1:8002` |
| MQTT | private Mosquitto 2.0.18, `127.0.0.1:1884`, anonymous loopback only |
| Broker storage | private `broker/`, persistence enabled, autosave 10 seconds |
| RES | separate process, delay 0.75 seconds, default 4096 command bound |
| Durable journals | private `state/ucs.sqlite` and `state/res.sqlite` |
| MQTT deadlines | operation 5 seconds; execution 30 seconds |
| Ollama | existing `eunbee-vlm-ollama`, `http://127.0.0.1:11435`, version 0.32.15 |
| Model | existing `ministral-3:3b`, Q4_K_M, digest `f04aa1c738f64e13c625b82ae92504fc0260fa6723b509ed1ece0fa188179b1d` |
| Role options | native tools; context 4096; output 512; timeout 60 seconds; temperature 0; seed 42; keep-alive 5m |
| Model template SHA256 | `24c6226aca35b16350187fb9125e7cb4e746795c78edd3cb1de45654c7e77181` |
| Ollama limits | memory 5368709120 bytes; memory+swap 5368709120 bytes; NanoCpus 0; restart policy `no`; unchanged |
| Speech | SpeechRecognition 3.14.6, private FLAC 1.4.3, language en-AU; explicit Google HTTPS endpoint |
| Access | Mac `http://localhost:8002`, SSH to Jetson loopback; no public listener/proxy |

Exact Python versions are in
[`requirements-jetson.txt`](../scripts/deployment/requirements-jetson.txt).
The model's stored temperature is 0.15; the application explicitly overrides it
with 0. No model, container, mounts or resource limits are changed by the launcher.

## Reproduce the private environment

The checkout must already contain the committed deployment branch. These commands
are for a fresh private environment, not an upgrade of running services:

```bash
ssh icp@100.73.141.62
umask 077
mkdir -p /home/icp/ucs-jetson-private/{packages,native}
cd /home/icp/ucs-jetson-private/packages
apt-get download mosquitto flac libmosquitto1 libdlt2 libwebsockets19t64 libcjson1 python3-pip-whl
for package in *.deb; do dpkg-deb -x "$package" ../native; done
python3 -m venv --without-pip /home/icp/ucs-jetson-private/venv
for wheel in ../native/usr/share/python-wheels/pip-*.whl; do
  PYTHONPATH="$wheel" /home/icp/ucs-jetson-private/venv/bin/python -m pip install "$wheel"
done
cd /home/icp/ucs-jetson-checkpoint
/home/icp/ucs-jetson-private/venv/bin/python -m pip install \
  -c scripts/deployment/requirements-jetson.txt -e '.[dev]'
/home/icp/ucs-jetson-private/venv/bin/python -m playwright install chromium
```

This uses Ubuntu's native package dependencies already present on this Jetson.
It is not a general installer for arbitrary Linux distributions. Original native
package versions: mosquitto/libmosquitto1 2.0.18-1build3; flac 1.4.3+ds-2.1ubuntu2;
libdlt2 2.18.10-10; libwebsockets19t64 4.3.3-1.1build3;
libcjson1 1.7.17-1; python3-pip-whl 24.0+dfsg-1ubuntu1.3.
Downloaded packages remain private. No root install/system broker is needed.

## Start, reconnect, inspect and stop

Start on Jetson (also use this after a Jetson reboot):

```bash
ssh icp@100.73.141.62 '/home/icp/ucs-jetson-checkpoint/scripts/deployment/jetson.sh start'
```

The existing Ollama container must already be running; the launcher will not
start/reconfigure it. Startup checks model presence, free ports, broker readiness,
RES subscription and the durable result listener. A failed or partial startup
stays visible for inspection; do not repeat `start` blindly. Services use transient
systemd user units with `Restart=no`; no boot activation or linger was changed.

Connect from the Mac (initial/reconnection command; run only if this tunnel is
absent):

```bash
ssh -M -S /private/tmp/ucs-jetson-8002.sock -fNT \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8002:127.0.0.1:8002 icp@100.73.141.62
```

Open **http://localhost:8002**. Use this hostname consistently to retain the same
browser session cookie. The origin is localhost; transport to Jetson is encrypted
SSH. Microphone permission must still be granted in the user's browser. This is
not a public HTTPS deployment.

```bash
# Mac tunnel status and application status
ssh -S /private/tmp/ucs-jetson-8002.sock -O check icp@100.73.141.62
curl -fsS http://localhost:8002/health
ssh icp@100.73.141.62 '/home/icp/ucs-jetson-checkpoint/scripts/deployment/jetson.sh status'
# Private logs, on Jetson
ls -lt /home/icp/ucs-jetson-private/logs/
# Stop application, RES, then broker; preserve all state
ssh icp@100.73.141.62 '/home/icp/ucs-jetson-checkpoint/scripts/deployment/jetson.sh stop'
# Disconnect only the new Mac tunnel
ssh -S /private/tmp/ucs-jetson-8002.sock -O exit icp@100.73.141.62
```

`/health` alone does not prove live model or execution readiness. Never delete
state or resubmit an uncertain command to make the UI appear healthy. Restart
using the same journal paths; inspect unresolved dispatches before rollback.

## Verification and recovery rehearsal

On Jetson, configure test dependencies without changing the serving configuration:

```bash
cd /home/icp/ucs-jetson-checkpoint
export PATH=/home/icp/ucs-jetson-private/venv/bin:/home/icp/ucs-jetson-private/native/usr/bin:/home/icp/ucs-jetson-private/native/usr/sbin:/usr/bin:/bin
export LD_LIBRARY_PATH=/home/icp/ucs-jetson-private/native/usr/lib/aarch64-linux-gnu
umask 077
pytest -q --basetemp="$(mktemp -d /home/icp/ucs-jetson-private/full-tests.XXXXXX)"
mypy --python-version 3.12
python -m pip check
pytest tests/test_recovery.py -q --basetemp="$(mktemp -d /home/icp/ucs-jetson-private/recovery.XXXXXX)"
```

The explicit mypy target matches this deployment. Default repository mypy targets
Python 3.9; the resolved AnyIO 4.15 dependency uses Python 3.10 syntax, so that
legacy-target check fails before checking project code. An obsolete LangGraph
ignore was removed for strict checking on the deployed dependencies. This does not
claim Python 3.9 compatibility of the new environment.

Recovery tests use **controlled roles**, isolated real brokers, separate simulated
RES processes, and new private state files. They cover clarification restart,
interrupted model work, process kills, queued/late correlated results, broker
outage/reconnection, duplicate suppression, unknown guards and no automatic
resend. They do not constitute live-model reliability testing or prove survival
of power loss, disk loss, broker queue loss or every executor crash.

The opt-in live smoke command is `python scripts/smoke_integrated.py
http://127.0.0.1:8002`. It makes live requests; do not treat reruns as unreported
retries. Record every attempt privately, including HTTP/model/validation/execution
failures. Deployment-specific live evidence remains outside Git.

## Rollback and open acceptance

To return to the preserved Mac setup, stop the three new units and disconnect the
new tunnel with the commands above. Open the previously running Mac demo at
`http://localhost:8001`. Mac port 8000 was not listening at inspection; do not imply
that a controlled demo was running there. Existing Mac services and model tunnel
were left untouched. No Git reset/checkout of the Mac working tree is required.

Keep the Jetson checkout/private state for diagnosis. Do not downgrade a checkout
under running services. A code rollback requires stopping first, reviewing
unsettled commands, selecting a known committed deployment revision and preserving
compatible state; deleting databases is not a recovery procedure.

Keep #2, #4, #5, #6, #7 and #12 open wherever acceptance remains missing. In
particular, a successful deployment does not establish model reliability, faithful
repairs, approved evaluation thresholds or full demo readiness. The compact UI
still omits the visible Google-processing disclosure documented in PR #15. Before
using the microphone, understand that Stop sends audio to Google; transcripts
must be reviewed and explicitly submitted. Real microphone/Google transcription,
actual Mac browser permission behavior, public HTTPS deployment and full broker
persistence/power-loss acceptance remain unverified. No fine-tuning, model change,
physical control or camera verification is included.

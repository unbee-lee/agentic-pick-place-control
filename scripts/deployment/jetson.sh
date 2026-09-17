#!/bin/bash
# Isolated, loopback-only Jetson demo. Never manages Ollama or system services.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PRIVATE=${UCS_JETSON_PRIVATE:-"$HOME/ucs-jetson-private"}
export PATH="$PRIVATE/venv/bin:$PRIVATE/native/usr/bin:$PRIVATE/native/usr/sbin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$PRIVATE/native/usr/lib/aarch64-linux-gnu"
export UCS_OLLAMA_URL=http://127.0.0.1:11435
export UCS_OLLAMA_MODEL=ministral-3:3b
export UCS_OLLAMA_TIMEOUT=60 UCS_OLLAMA_CONTEXT=4096 UCS_OLLAMA_OUTPUT=512
export UCS_MQTT_HOST=127.0.0.1 UCS_MQTT_PORT=1884
export UCS_MQTT_OPERATION_TIMEOUT=5 UCS_MQTT_EXECUTION_TIMEOUT=30 UCS_RES_DELAY=0.75
export UCS_SPEECH_LANGUAGE=en-AU
export UCS_STATE_PATH="$PRIVATE/state/ucs.sqlite"
export UCS_RES_STATE_PATH="$PRIVATE/state/res.sqlite"
cd "$ROOT"
umask 077
case "${1:-status}" in
  run-broker) exec mosquitto -c "$PRIVATE/mosquitto.conf" ;;
  run-res) exec python -m ucs_app.simulated_res ;;
  run-app) exec uvicorn ucs_app.live:create_integrated_app --factory --host 127.0.0.1 --port 8002 ;;
  start)
    test -x "$PRIVATE/venv/bin/python"
    test -x "$PRIVATE/native/usr/sbin/mosquitto"
    test -x "$PRIVATE/native/usr/bin/flac"
    # Refuse partial/repeated starts; inspect or stop our units explicitly first.
    for service in broker res app; do
      if systemctl --user is-active --quiet "ucs-checkpoint-$service"; then
        echo "Already active: ucs-checkpoint-$service; inspect status first" >&2; exit 1
      fi
    done
    python - <<'PY'
import json, socket, urllib.request
with urllib.request.urlopen('http://127.0.0.1:11435/api/tags', timeout=5) as response:
    assert any(m['name'] == 'ministral-3:3b' for m in json.load(response)['models'])
for port in (1884, 8002):
    with socket.socket() as probe:
        # Match server restart semantics while still rejecting active listeners.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))
PY
    mkdir -p "$PRIVATE/state" "$PRIVATE/broker" "$PRIVATE/logs"
    cat > "$PRIVATE/mosquitto.conf" <<CONFIG
listener 1884 127.0.0.1
allow_anonymous true
persistence true
persistence_location $PRIVATE/broker/
autosave_interval 10
CONFIG
    for service in broker res app; do
      # Separate log per start, so an old readiness message cannot pass a new check.
      log="$PRIVATE/logs/$service-$(date +%s%N).log"
      systemd-run --user --collect --unit="ucs-checkpoint-$service" \
        --property=Restart=no --property=UMask=0077 \
        --property="StandardOutput=append:$log" --property="StandardError=append:$log" \
        --setenv="UCS_JETSON_PRIVATE=$PRIVATE" "$ROOT/scripts/deployment/jetson.sh" "run-$service"
      python - "$service" "$log" <<'PY'
import json, pathlib, socket, sys, time, urllib.request
service, log = sys.argv[1:]
for _ in range(100):
    try:
        if service == 'broker':
            with socket.create_connection(('127.0.0.1', 1884), timeout=.2): pass
        elif service == 'res':
            assert 'Simulated RES ready' in pathlib.Path(log).read_text()
        else:
            with urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=.2) as response:
                assert json.load(response)['result_listener'] == 'ready'
        break
    except (OSError, AssertionError):
        time.sleep(.2)
else:
    raise SystemExit(f'{service} readiness failed; inspect {log}; no automatic retry/cleanup')
print(f'{service} ready')
PY
    done ;;
  stop)
    # Preserve journals and broker queues. Never reset uncertain outcomes.
    systemctl --user stop ucs-checkpoint-app.service ucs-checkpoint-res.service
    systemctl --user stop ucs-checkpoint-broker.service ;;
  status)
    systemctl --user --no-pager status ucs-checkpoint-broker ucs-checkpoint-res ucs-checkpoint-app ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac

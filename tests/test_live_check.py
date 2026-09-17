"""The live-check reporter must never invent a successful clarification path."""

import json
import subprocess
import sys

import pytest

from test_integrated import Evidence, integrated
from test_mqtt import broker, broker_port


@pytest.mark.parametrize("integrated", ["malformed"], indirect=True)
def test_live_check_skips_reply_when_no_question_was_produced(integrated: tuple[str, Evidence]) -> None:
    url, evidence = integrated
    result = subprocess.run([sys.executable, "scripts/check_live_clarification.py", url],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    reports = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    attempted = [report for report in reports if report.get("status") == "STARTED"]
    assert [report["attempt"] for report in attempted] == ["explicit_complete", "remaining_slot", "clarification"]
    replies = [report for report in reports if report["attempt"] == "actual_reply"]
    assert len(replies) == 1 and replies[0]["status"] == "NOT_RUN"
    assert len(evidence.calls) == 3 and not evidence.commands
    assert "PRIVATE" not in result.stdout

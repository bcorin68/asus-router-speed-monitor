#!/usr/bin/env python3
"""Collect router-side Ookla results. Python standard library only."""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCHEMA = 1


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def number(value, field, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"Missing or invalid {field}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {field}")
    return value


def parse_result(output, observed=None):
    # Embedded Ookla emits progress/log records followed by a final result.
    candidates = []
    try:
        candidates.append(json.loads(output))
    except ValueError:
        pass
    for line in output.splitlines():
        try:
            candidates.append(json.loads(line))
        except ValueError:
            continue
    result = next((obj for obj in reversed(candidates)
                   if isinstance(obj, dict) and obj.get("type") == "result"), None)
    if result is None:
        raise ValueError("Ookla did not return a final result record")
    download = result.get("download") or {}
    upload = result.get("upload") or {}
    ping = result.get("ping") or {}
    server = result.get("server") or {}
    timestamp = observed or utc_now()
    test = {
        "timestamp": iso(timestamp),
        "status": "ok",
        "download_gbps": number(download.get("bandwidth"), "download bandwidth") * 8 / 1e9,
        "upload_gbps": number(upload.get("bandwidth"), "upload bandwidth") * 8 / 1e9,
        "latency_ms": number(ping.get("latency"), "latency"),
        "jitter_ms": number(ping.get("jitter"), "jitter", optional=True),
        "packet_loss_pct": number(result.get("packetLoss"), "packet loss", optional=True),
        "download_bytes": number(download.get("bytes"), "download bytes", optional=True),
        "upload_bytes": number(upload.get("bytes"), "upload bytes", optional=True),
        "server": str(server.get("name", "Unknown"))[:160],
        "server_location": str(server.get("location", ""))[:160],
        "error": None,
    }
    # Do not publish the raw result: it may include the home's public IP or MAC.
    return test, result


def atomic_write(path, text, mode=0o644):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_record(state, config, record, raw=None, now=None):
    state = Path(state)
    now = now or utc_now()
    history_path = state / "www" / "history.json"
    tests = []
    if history_path.exists():
        # Fail rather than overwrite an unreadable existing history.
        old = json.loads(history_path.read_text(encoding="utf-8"))
        if old.get("schema") != SCHEMA or not isinstance(old.get("tests"), list):
            raise ValueError("Existing history has an unknown format")
        tests = old["tests"]
    cutoff = now - timedelta(days=config.get("retention_days", 90))
    tests = [item for item in tests if datetime.fromisoformat(
        item["timestamp"].replace("Z", "+00:00")) >= cutoff]
    tests.append(record)
    tests = tests[-5000:]
    doc = {"schema": SCHEMA, "updated_at": iso(now),
           "schedule": "Every six hours at 00:07, 06:07, 12:07 and 18:07 America/New_York",
           "retention_days": config.get("retention_days", 90),
           "source": "ASUS router", "tests": tests}
    if raw is not None:
        atomic_write(state / "private" / "last-result.json",
                     json.dumps(raw, indent=2, allow_nan=False) + "\n", 0o600)
    atomic_write(history_path, json.dumps(doc, allow_nan=False) + "\n")
    rows = io.StringIO(newline="")
    fields = ["timestamp", "status", "download_gbps", "upload_gbps", "latency_ms",
              "jitter_ms", "packet_loss_pct", "download_bytes", "upload_bytes",
              "server", "server_location", "error"]
    writer = csv.DictWriter(rows, fieldnames=fields)
    writer.writeheader()
    for test in tests:
        # Keep CSV text safe when opened in a spreadsheet.
        safe = {key: ("'" + val if isinstance(val, str) and val.startswith(
            ("=", "+", "-", "@", "\t", "\r")) else val) for key, val in test.items()}
        writer.writerow(safe)
    atomic_write(state / "www" / "results.csv", rows.getvalue())


def ssh_command(config):
    remote = ("if pidof ookla >/dev/null 2>&1; then "
              "echo 'A router speed test is already running' >&2; exit 75; fi; "
              "exec ookla -c https://config.speedtest.net/v1/embed/4tiouex6gsnzq55o/config "
              "-f jsonl --progress=no --cpu-metrics=yes")
    return ["/usr/bin/ssh", "-T", "-i", config["ssh_key"],
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            "-o", "UserKnownHostsFile=" + config["known_hosts"],
            config["router_user"] + "@" + config["router_host"], remote]


def collect(config):
    import fcntl  # Linux only; parsing and storage tests also run on Windows.
    state = Path(config["state_dir"])
    with (state / "private" / "collection.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Collection is already running; no second test started.")
            return 0
        raw = None
        try:
            completed = subprocess.run(ssh_command(config), capture_output=True,
                                       text=True, errors="replace", timeout=300, check=False)
            if completed.returncode == 75:
                record = {"timestamp": iso(utc_now()), "status": "skipped",
                          "error": "Router speed test already running; this test was skipped."}
            elif completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or
                                    f"SSH exited with code {completed.returncode}").strip()[-400:])
            else:
                record, raw = parse_result(completed.stdout)
        except subprocess.TimeoutExpired:
            record = {"timestamp": iso(utc_now()), "status": "failed",
                      "error": "Router test did not finish within five minutes."}
        except (OSError, ValueError, RuntimeError) as exc:
            record = {"timestamp": iso(utc_now()), "status": "failed", "error": str(exc)[:400]}
        save_record(state, config, record, raw)
        print(json.dumps(record, allow_nan=False))
        return 1 if record["status"] == "failed" else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/router-speed-monitor.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    return collect(config)


if __name__ == "__main__":
    sys.exit(main())

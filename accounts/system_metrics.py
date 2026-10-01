"""Small, read-only snapshot of the Linux host running the panel."""

from pathlib import Path
import os
import shutil
import time


def _percent(used, total):
    return round(100 * used / total) if total else None


def _size(bytes_count):
    return f"{bytes_count / (1024 ** 3):.1f} GiB"


def _cpu_times():
    fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 9:
        raise ValueError("CPU counters unavailable")
    counters = [int(value) for value in fields[1:9]]
    return sum(counters), counters[3] + counters[4]


def _cpu_metric():
    before_total, before_idle = _cpu_times()
    time.sleep(0.5)
    after_total, after_idle = _cpu_times()
    total = after_total - before_total
    used = total - (after_idle - before_idle)
    return {
        "percent": round(max(0, min(100, 100 * used / total)), 1) if total > 0 else None,
        "detail": f"{os.cpu_count() or 1} CPU cores · live usage",
    }


def _memory_metric():
    fields = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        name, _, value = line.partition(":")
        if name in {"MemTotal", "MemAvailable"}:
            fields[name] = int(value.split()[0]) * 1024
    total = fields["MemTotal"]
    used = total - fields["MemAvailable"]
    return {"percent": _percent(used, total), "detail": f"{_size(used)} of {_size(total)}"}


def _disk_metric():
    disk = shutil.disk_usage("/")
    return {
        "percent": _percent(disk.used, disk.total),
        "detail": f"{_size(disk.used)} of {_size(disk.total)}",
    }


def get_system_metrics():
    result = {}
    for name, collector in (("cpu", _cpu_metric), ("memory", _memory_metric), ("disk", _disk_metric)):
        try:
            result[name] = collector()
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            result[name] = {"percent": None, "detail": "Unavailable"}
    return result

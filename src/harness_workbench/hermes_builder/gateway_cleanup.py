"""Stop only the gateway recorded inside this capture's fresh Hermes home.

Executed with the verified Hermes Python, with the pinned source explicitly on
sys.path. Never scans other profiles or invokes global gateway/service commands.
"""
import json
import os
from pathlib import Path
import sys
import time


def cleanup(home, status, planned_stop, wait_seconds=5):
    path = home / 'gateway.pid'
    if not path.exists():
        return {'passed': True, 'state': 'no-owned-pid-record'}
    if path.is_symlink():
        raise ValueError('gateway PID record is a symlink')
    record = json.loads(path.read_text())
    pid = record.get('pid')
    if (record.get('kind') != 'hermes-gateway' or type(pid) is not int or pid <= 1
            or pid in (os.getpid(), os.getppid()) or record.get('hermes_home') != str(home)):
        raise ValueError('gateway ownership record does not match this run')
    if not status._pid_exists(pid):
        return {'passed': True, 'state': 'already-stopped', 'pid': pid}
    recorded = record.get('start_time')
    current = status.get_process_start_time(pid)
    if (recorded is None or current is None or not status.start_time_fingerprints_match(recorded, current)
            or not status._record_matches_live_gateway_pid(record, pid, expected_home=home)):
        raise ValueError('live gateway identity could not be verified')
    planned_stop(home, pid, current)
    # Recheck identity immediately before each signal; upstream's graceful
    # terminate_pid does not itself enforce expected_start_time on POSIX.
    def signal(force):
        fresh = status.get_process_start_time(pid)
        if fresh is None or not status.start_time_fingerprints_match(current, fresh):
            raise ValueError('gateway identity changed before cleanup')
        status.terminate_pid(pid, force=force, expected_start_time=fresh)
    def wait():
        deadline = time.monotonic() + wait_seconds
        while status._pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        return not status._pid_exists(pid)
    signal(False)
    forced = False
    if not wait():
        forced = True
        signal(True)
        wait()
    alive = status._pid_exists(pid)
    return {'passed': not alive, 'state': 'stopped' if not alive else 'survived',
            'pid': pid, 'start_time': current, 'forced': forced, 'alive_after_cleanup': alive}


def main():
    sys.path.insert(0, str(Path(sys.argv[1]).resolve(strict=True)))
    from gateway import status
    from hermes_cli.gateway_runtime_replace import _write_planned_stop
    try:
        result = cleanup(Path(sys.argv[2]).resolve(strict=True), status, _write_planned_stop)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {'passed': False, 'error': str(error)}
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

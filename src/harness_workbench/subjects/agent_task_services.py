#!/usr/bin/env python3
"""Authenticated process services and supervisor for guarded agent-task runs."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
import hashlib
import json
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from typing import Any, Callable

from harness_workbench import capture as capture_module

from agent_task_broker import (
    SpawnBroker,
    append_registry_row,
    witness_abnormal_termination,
)
from agent_task_authorization import (
    AuthorizationError,
    AuthorizationExpectation,
    OneAttemptAuthorizer,
    create_live_topology,
    load_authorization_key,
    validate_bound_files,
    validate_live_topology,
    validate_release_configuration,
)
from agent_task_control import CallControl, ControlError, Permit
from agent_task_process import platform_start_identity, process_executable
from agent_task_schema import PROCESS_REGISTRY_SCHEMA


HERE = Path(__file__).resolve().parent
SERVICE_PROGRAM = Path(__file__).resolve()
PACKAGE_ROOT = HERE.parents[1]
DEFAULT_STARTUP_SECONDS = 5.0
DEFAULT_RPC_SECONDS = 45.0


class ServiceError(RuntimeError):
    """An authenticated control-plane service is unavailable or refused."""


def _write_json_exclusive(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_secret(path: Path) -> bytes:
    metadata = path.stat()
    if metadata.st_mode & 0o077:
        raise ServiceError("service authentication file is not owner-only")
    secret = path.read_bytes()
    if len(secret) != 32:
        raise ServiceError("service authentication key has the wrong length")
    return secret


def _executable_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


class _ServiceProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        pid, status = os.waitpid(self.pid, os.WNOHANG)
        if pid:
            self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.poll()
            if status is not None:
                return status
            time.sleep(0.01)
        raise TimeoutError(f"service process {self.pid} did not exit")


def _wait_socket(path: Path, process: _ServiceProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        status = process.poll()
        if status is not None:
            raise ServiceError(f"control-plane service exited during startup: {status}")
        time.sleep(0.01)
    raise ServiceError(f"control-plane service did not become ready: {path}")


def _rpc(socket_path: Path, authkey: bytes, request: dict[str, Any]) -> Any:
    try:
        connection = Client(str(socket_path), family="AF_UNIX", authkey=authkey)
        try:
            connection.send(request)
            response = connection.recv()
        finally:
            connection.close()
    except (EOFError, OSError, ConnectionError) as error:
        raise ServiceError(f"authenticated service request failed: {error}") from error
    if type(response) is not dict or set(response) != {"ok", "value"}:
        raise ServiceError("authenticated service returned a malformed reply")
    if response["ok"] is not True:
        raise ServiceError(str(response["value"]))
    return response["value"]


def _send_without_reply(
    socket_path: Path, authkey: bytes, request: dict[str, Any]
) -> None:
    connection = Client(str(socket_path), family="AF_UNIX", authkey=authkey)
    try:
        connection.send(request)
    finally:
        connection.close()


def _permit(value: dict[str, Any]) -> Permit:
    return Permit(**value)


class CallControlClient:
    """The runtime-facing API for the independent call-control process."""

    def __init__(self, socket_path: Path, authkey: bytes, journal: Path | None = None) -> None:
        self.socket_path = socket_path
        self.authkey = authkey
        self.journal = journal

    def request(
        self,
        *,
        phase: str,
        subject: str,
        store_nonce: str,
        request_id: str,
        usage_gate: Callable[[], tuple[dict[str, Any], bool]] | None = None,
        retry_of: int | None = None,
    ) -> Permit:
        if usage_gate is not None:
            # Usage belongs to the service. Accepting a caller-supplied reader
            # would move the permit-time gate back across the trust boundary.
            raise ServiceError("independent call control owns its usage reader")
        value = _rpc(self.socket_path, self.authkey, {
            "op": "request", "phase": phase, "subject": subject,
            "store_nonce": store_nonce, "request_id": request_id,
            "retry_of": retry_of,
        })
        return _permit(value)

    def request_without_reply(self, **values: Any) -> None:
        _send_without_reply(
            self.socket_path, self.authkey, {"op": "request", **values}
        )

    def release(
        self, permit: Permit, *, authorization_path: Path | None = None
    ) -> dict[str, Any] | None:
        return _rpc(self.socket_path, self.authkey, {
            "op": "release", "permit": asdict(permit),
            "authorization_path": (
                str(authorization_path) if authorization_path is not None else None
            ),
        })

    def complete(self, permit: Permit, *, result: str, cleanup_proved: bool) -> None:
        _rpc(self.socket_path, self.authkey, {
            "op": "complete", "permit": asdict(permit), "result": result,
            "cleanup_proved": cleanup_proved,
        })

    def authorize_phase(self, phase: str, *, maximum_calls: int) -> None:
        _rpc(self.socket_path, self.authkey, {
            "op": "authorize_phase", "phase": phase, "maximum_calls": maximum_calls,
        })

    def latch_stop(self, reason: str) -> None:
        _rpc(self.socket_path, self.authkey, {"op": "latch_stop", "reason": reason})

    def expire(self) -> None:
        _rpc(self.socket_path, self.authkey, {"op": "expire"})

    def refusal(self, *, subject: str, store_nonce: str) -> dict[str, Any]:
        return _rpc(self.socket_path, self.authkey, {
            "op": "refusal", "subject": subject, "store_nonce": store_nonce,
        })

    def status(self) -> dict[str, Any]:
        return _rpc(self.socket_path, self.authkey, {"op": "status"})

    def shutdown(self) -> dict[str, Any]:
        return _rpc(self.socket_path, self.authkey, {"op": "shutdown"})


class BrokerClient:
    """The runtime-facing API for the independent spawn-broker process."""

    def __init__(self, socket_path: Path, authkey: bytes, registry: Path) -> None:
        self.socket_path = socket_path
        self.authkey = authkey
        self.registry = registry
        self.receipts: list[dict[str, Any]] = []

    def launch(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        phase: str,
        timeout: float,
        stdout_limit: int,
        stderr_limit: int,
        redactions: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        value = _rpc(self.socket_path, self.authkey, {
            "op": "launch", "argv": argv, "cwd": str(cwd), "env": env,
            "phase": phase, "timeout": timeout, "stdout_limit": stdout_limit,
            "stderr_limit": stderr_limit, "redactions": list(redactions),
        })
        capture, receipt = value["capture"], value["receipt"]
        self.receipts.append(receipt)
        return capture, receipt

    def status(self) -> dict[str, Any]:
        return _rpc(self.socket_path, self.authkey, {"op": "status"})

    def shutdown(self) -> dict[str, Any]:
        return _rpc(self.socket_path, self.authkey, {"op": "shutdown"})


def _gate_snapshot(reading: dict[str, Any], limits: dict[str, int]) -> bool:
    if not reading.get("metered"):
        return True
    windows = reading.get("windows")
    if type(windows) is not dict:
        return False
    for name, ceiling in limits.items():
        row = windows.get(name)
        if type(row) is not dict or type(row.get("percent")) is not int:
            return False
        if row["percent"] >= ceiling:
            return False
    return True


class _UsageReader:
    def __init__(self, config: dict[str, Any]) -> None:
        self.mode = config["usage_mode"]
        self.limits = config["usage_limits"]
        self.snapshots = deque(config.get("usage_snapshots", []))
        self.usage_dir = Path(config["usage_dir"])
        self.usage_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.ordinal = 0

    def __call__(self) -> tuple[dict[str, Any], bool]:
        if self.mode == "injected":
            if not self.snapshots:
                raise ServiceError("injected usage sequence is exhausted")
            reading = self.snapshots.popleft()
        elif self.mode == "gateway":
            import usage_probe
            reading = usage_probe.snapshot()
        else:
            raise ServiceError(f"unknown service usage mode: {self.mode}")
        if type(reading) is not dict:
            raise ServiceError("usage reader returned a non-object")
        path = self.usage_dir / f"permit-{self.ordinal:04d}.json"
        self.ordinal += 1
        _write_json_exclusive(path, reading)
        return reading, _gate_snapshot(reading, self.limits)


def _watch_supervisor(
    fd: int,
    closing: threading.Event,
    abnormal: Callable[[], None],
) -> None:
    try:
        while os.read(fd, 1):
            pass
    except OSError:
        pass
    finally:
        os.close(fd)
    if not closing.is_set():
        try:
            abnormal()
        finally:
            os._exit(72)


def _serve(
    socket_path: Path,
    authkey: bytes,
    dispatch: Callable[[dict[str, Any]], tuple[Any, bool]],
) -> None:
    if socket_path.exists():
        raise ServiceError(f"service socket already exists: {socket_path}")
    listener = Listener(str(socket_path), family="AF_UNIX", authkey=authkey)
    os.chmod(socket_path, 0o600)
    try:
        stop = False
        while not stop:
            connection = listener.accept()
            try:
                try:
                    request = connection.recv()
                    if type(request) is not dict or type(request.get("op")) is not str:
                        raise ServiceError("authenticated request is malformed")
                    value, stop = dispatch(request)
                    response = {"ok": True, "value": value}
                except Exception as error:
                    response = {"ok": False, "value": f"{type(error).__name__}: {error}"}
                try:
                    connection.send(response)
                except (BrokenPipeError, EOFError, OSError):
                    pass
            finally:
                connection.close()
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def _call_control_server(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    control = CallControl(
        Path(config["journal"]), campaign_nonce=config["campaign_nonce"],
        maximum_calls=config["maximum_calls"],
        authorized_phases=set(config["authorized_phases"]),
        phase_maximums=config.get("phase_maximums"),
        lease_seconds=config["lease_seconds"],
    )
    usage = _UsageReader(config)
    release_config = config.get("release_authorization")
    authorizer = None
    if release_config is not None:
        release_config = validate_release_configuration(
            release_config, require_destination_nonexistent=False
        )
        authorizer = OneAttemptAuthorizer(
            Path(release_config["consumed_dir"]),
            key=load_authorization_key(Path(release_config["key_file"])),
        )
    closing = threading.Event()

    def abnormal() -> None:
        try:
            control.latch_stop("supervisor_channel_eof")
            control.close()
        except ControlError:
            pass

    threading.Thread(
        target=_watch_supervisor,
        args=(args.liveness_fd, closing, abnormal), daemon=True,
    ).start()

    def dispatch(request: dict[str, Any]) -> tuple[Any, bool]:
        operation = request["op"]
        if operation == "request":
            permit = control.request(
                phase=request["phase"], subject=request["subject"],
                store_nonce=request["store_nonce"], request_id=request["request_id"],
                retry_of=request.get("retry_of"), usage_gate=usage,
            )
            return asdict(permit), False
        if operation == "release":
            permit = _permit(request["permit"])
            artifact_path = request.get("authorization_path")
            receipt = None
            if authorizer is None:
                if artifact_path is not None:
                    raise ServiceError(
                        "authorization artifact supplied to an offline control plane"
                    )
            else:
                if type(artifact_path) is not str or not artifact_path:
                    control.latch_stop("release_authorization_missing")
                    raise ServiceError(
                        "provider release requires a one-attempt authorization artifact"
                    )
                route = release_config["routes"].get(permit.subject)
                if type(route) is not dict:
                    control.latch_stop("release_authorization_route_unbound")
                    raise ServiceError("provider release has no bound route")
                expectation = AuthorizationExpectation.from_permit(
                    permit,
                    execution_plan_sha256=release_config["execution_plan_sha256"],
                    provider_route_sha256=route["provider_route_sha256"],
                    model=route["model"],
                )
                try:
                    validate_live_topology(
                        Path(release_config["execution_plan"]["destination"]["resolved"]),
                        phase=permit.phase,
                    )
                    validate_bound_files(release_config["bound_files"])
                except AuthorizationError:
                    control.latch_stop("release_topology_or_input_drift")
                    raise
                try:
                    receipt = authorizer.consume(Path(artifact_path), expectation)
                except AuthorizationError:
                    control.latch_stop("release_authorization_rejected")
                    raise
            # Consumption deliberately precedes release. If release then fails,
            # the authorization remains spent and cannot cause a repeated call.
            control.release(permit)
            return receipt, False
        if operation == "complete":
            control.complete(
                _permit(request["permit"]), result=request["result"],
                cleanup_proved=request["cleanup_proved"],
            )
            return None, False
        if operation == "authorize_phase":
            control.authorize_phase(request["phase"], maximum_calls=request["maximum_calls"])
            return None, False
        if operation == "latch_stop":
            control.latch_stop(request["reason"])
            return None, False
        if operation == "expire":
            control.expire()
            return None, False
        if operation == "refusal":
            return control.refusal(
                subject=request["subject"], store_nonce=request["store_nonce"]
            ), False
        if operation == "status":
            return {
                "state": control.state, "allocated_calls": control.allocated_calls,
            }, False
        if operation == "shutdown":
            control.close()
            closing.set()
            return {"kind": "clean_self_issued"}, True
        raise ServiceError(f"unknown call-control operation: {operation}")

    _serve(args.socket, _load_secret(args.secret_file), dispatch)
    return 0


def _broker_server(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    broker = SpawnBroker(Path(config["registry"]), python=Path(config["python"]))
    closing = threading.Event()

    def abnormal() -> None:
        stop = Path(config["service_stop"])
        try:
            witness_abnormal_termination(
                broker.registry, stop, child="supervisor", reason="channel_eof"
            )
        except (FileExistsError, OSError, ValueError):
            pass

    threading.Thread(
        target=_watch_supervisor,
        args=(args.liveness_fd, closing, abnormal), daemon=True,
    ).start()

    def dispatch(request: dict[str, Any]) -> tuple[Any, bool]:
        operation = request["op"]
        if operation == "launch":
            capture, receipt = broker.launch(
                request["argv"], cwd=Path(request["cwd"]), env=request["env"],
                phase=request["phase"], timeout=request["timeout"],
                stdout_limit=request["stdout_limit"],
                stderr_limit=request["stderr_limit"],
                redactions=tuple(request["redactions"]),
            )
            return {"capture": capture, "receipt": receipt}, False
        if operation == "status":
            return {"closed": broker.closed, "receipts": len(broker.receipts)}, False
        if operation == "shutdown":
            receipt = broker.close()
            closing.set()
            return receipt, True
        raise ServiceError(f"unknown broker operation: {operation}")

    _serve(args.socket, _load_secret(args.secret_file), dispatch)
    return 0


class ControlPlaneSupervisor:
    """Own two authenticated service processes and witness either one's death."""

    def __init__(
        self,
        session: Path,
        *,
        campaign_nonce: str,
        maximum_calls: int,
        authorized_phases: set[str],
        phase_maximums: dict[str, int] | None = None,
        lease_seconds: float = 30.0,
        usage_mode: str = "injected",
        usage_limits: dict[str, int] | None = None,
        usage_snapshots: list[dict[str, Any]] | None = None,
        release_authorization: dict[str, Any] | None = None,
        startup_seconds: float = DEFAULT_STARTUP_SECONDS,
    ) -> None:
        if usage_mode == "gateway" and release_authorization is None:
            raise ServiceError(
                "gateway control-plane release requires a separately validated authorization artifact"
            )
        if release_authorization is not None:
            release_authorization = validate_release_configuration(
                release_authorization, require_destination_nonexistent=True
            )
            destination = Path(
                release_authorization["execution_plan"]["destination"]["resolved"]
            )
            expected_session = destination / "session"
            if session != expected_session:
                raise ServiceError(
                    "authorized live control plane must use the planned session root"
                )
            create_live_topology(destination)
        self.session = session
        if release_authorization is None:
            session.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.stop_record = session / "supervisor-stop.json"
        self.registry = session / "process-registry.jsonl"
        self.journal = session / "call-control.jsonl"
        self._closed = False
        self._witnessed = False
        secret = secrets.token_bytes(32)
        secret_file = session / ".service-auth"
        descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, secret)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        call_config = session / "call-control-service.json"
        broker_config = session / "broker-service.json"
        _write_json_exclusive(call_config, {
            "journal": str(self.journal), "campaign_nonce": campaign_nonce,
            "maximum_calls": maximum_calls,
            "authorized_phases": sorted(authorized_phases),
            "phase_maximums": phase_maximums or {}, "lease_seconds": lease_seconds,
            "usage_mode": usage_mode,
            "usage_limits": usage_limits or {"rolling": 80, "weekly": 90},
            "usage_snapshots": usage_snapshots or [],
            "usage_dir": str(session / "permit-usage"),
            "release_authorization": release_authorization,
        })
        _write_json_exclusive(broker_config, {
            "registry": str(self.registry), "python": str(Path(sys.executable).resolve()),
            "service_stop": str(session / "broker-supervisor-eof.json"),
        })
        # Darwin's AF_UNIX path ceiling is short enough that a retained run
        # destination can exceed it. Sockets are ephemeral control channels,
        # not evidence, so keep them in one owner-only short temporary root.
        self._socket_root = Path(tempfile.mkdtemp(prefix="hwb-agent-task-service-"))
        os.chmod(self._socket_root, 0o700)
        self.call_socket = self._socket_root / "call.sock"
        self.broker_socket = self._socket_root / "broker.sock"
        self._call_read, self._call_write = os.pipe()
        self._broker_read, self._broker_write = os.pipe()
        self.call_process = self._spawn(
            "call-control", self.call_socket, secret_file, call_config, self._call_read
        )
        self.broker_process = self._spawn(
            "broker", self.broker_socket, secret_file, broker_config, self._broker_read
        )
        os.close(self._call_read)
        os.close(self._broker_read)
        try:
            _wait_socket(self.call_socket, self.call_process, startup_seconds)
            _wait_socket(self.broker_socket, self.broker_process, startup_seconds)
        except Exception:
            self._terminate_children()
            self._discard_service_sockets()
            raise
        finally:
            secret_file.unlink(missing_ok=True)
        self.control = CallControlClient(self.call_socket, secret, self.journal)
        self.broker = BrokerClient(self.broker_socket, secret, self.registry)
        self.assert_live()
        self._record_control_plane("call_control", self.call_process)
        self._record_control_plane("broker", self.broker_process)

    def _record_control_plane(self, child: str, process: _ServiceProcess) -> None:
        executable = process_executable(process.pid)
        start_identity = platform_start_identity(process.pid)
        if executable is None or start_identity is None or os.getpgid(process.pid) != process.pid:
            self._witness(child, "control_plane_identity_unavailable")
            raise ServiceError(f"cannot bind {child} control-plane process identity")
        append_registry_row(self.registry, {
            "schema": PROCESS_REGISTRY_SCHEMA,
            "event": "control_plane_registered",
            "kind": "supervisor_observed",
            "control_plane_child": child,
            "pid": process.pid,
            "pgid": process.pid,
            "platform_start_identity": start_identity,
            "executable_identity": _executable_sha256(executable),
        })

    def _spawn(
        self, service: str, socket_path: Path, secret_file: Path,
        config: Path, liveness_fd: int,
    ) -> _ServiceProcess:
        pid = os.fork()
        if pid:
            return _ServiceProcess(pid)
        try:
            os.setsid()
            for descriptor in (
                self._call_read, self._call_write,
                self._broker_read, self._broker_write,
            ):
                if descriptor != liveness_fd:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            arguments = argparse.Namespace(
                service=service, socket=socket_path, secret_file=secret_file,
                config=config, liveness_fd=liveness_fd,
            )
            status = (
                _call_control_server(arguments)
                if service == "call-control"
                else _broker_server(arguments)
            )
        except BaseException:
            status = 1
        os._exit(status)

    def assert_live(self) -> None:
        if self._closed:
            raise ServiceError("control-plane supervisor is closed")
        failures = [
            ("call_control", self.call_process.poll()),
            ("broker", self.broker_process.poll()),
        ]
        for child, status in failures:
            if status is not None:
                self._witness(child, f"unexpected_exit_{status}")
                raise ServiceError(f"{child} service exited unexpectedly: {status}")
        try:
            self.control.status()
            self.broker.status()
        except ServiceError as error:
            child = "call_control" if self.call_process.poll() is not None else "broker"
            self._witness(child, "authenticated_channel_failure")
            raise ServiceError(f"control-plane liveness check failed: {error}") from error

    def _witness(self, child: str, reason: str) -> None:
        if self._witnessed:
            return
        self._witnessed = True
        if child != "call_control" and self.call_process.poll() is None:
            try:
                self.control.latch_stop(f"{child}_{reason}")
            except ServiceError:
                pass
        self._terminate_children()
        if self.registry.exists() and not self.stop_record.exists():
            witness_abnormal_termination(
                self.registry, self.stop_record, child=child, reason=reason
            )
        self._closed = True
        for name in ("_broker_write", "_call_write"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self._discard_service_sockets()

    def _discard_service_sockets(self) -> None:
        for path in (self.call_socket, self.broker_socket):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            self._socket_root.rmdir()
        except FileNotFoundError:
            pass

    def _terminate_children(self) -> None:
        for process in (getattr(self, "call_process", None), getattr(self, "broker_process", None)):
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, capture_module.signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in (getattr(self, "call_process", None), getattr(self, "broker_process", None)):
            if process is None:
                continue
            try:
                process.wait(timeout=2.0)
            except TimeoutError:
                try:
                    os.killpg(process.pid, capture_module.signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2.0)

    def close(self) -> dict[str, Any]:
        self.assert_live()
        broker_receipt = self.broker.shutdown()
        call_receipt = self.control.shutdown()
        self._closed = True
        for descriptor in (self._broker_write, self._call_write):
            os.close(descriptor)
        self.broker_process.wait(timeout=5.0)
        self.call_process.wait(timeout=5.0)
        if self.broker_process.returncode != 0 or self.call_process.returncode != 0:
            raise ServiceError("a control-plane service did not shut down cleanly")
        for child, process in (
            ("broker", self.broker_process),
            ("call_control", self.call_process),
        ):
            append_registry_row(self.registry, {
                "schema": PROCESS_REGISTRY_SCHEMA,
                "event": "control_plane_shutdown",
                "kind": "clean_self_issued",
                "control_plane_child": child,
                "returncode": process.returncode,
            })
        self._discard_service_sockets()
        return {"broker": broker_receipt, "call_control": call_receipt}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=("call-control", "broker"), required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--liveness-fd", type=int, required=True)
    args = parser.parse_args()
    if args.service == "call-control":
        return _call_control_server(args)
    return _broker_server(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Fixed native service operations and fresh, PID-bound host health observations."""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time

from .models import LifecycleBlockedError
from .service_control import CommandResult, LaunchdServiceAdapter, ServicePreflight


def _run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=240)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def runtime_root(deployment: Path) -> Path:
    marker = deployment / "shared-release.path"
    if marker.exists():
        from .cell_deployment import validate_cell_deployment

        validate_cell_deployment(deployment)
        return Path(marker.read_text().strip()).resolve(strict=True)
    return deployment.resolve(strict=True)


def service_binding(unit, candidate):
    return {**unit, **(unit.get("candidate", {}) if candidate else {})}


def validate_loaded_launchd(raw, unit):
    for field, expected in (
        ("program", unit["program"]),
        ("working directory", unit["workdir"]),
    ):
        matches = re.findall(r"^\s*" + re.escape(field) + r" = (.+?)\s*$", raw, re.M)
        if matches != [expected]:
            raise LifecycleBlockedError(
                "service_loaded_binding",
                "loaded launchd definition differs from approved bindings",
            )
    profiles = re.findall(r"^\s*HERMES_HOME => (.+?)\s*$", raw, re.M)
    if profiles != ([] if unit.get("profile") is None else [unit["profile"]]):
        raise LifecycleBlockedError(
            "service_loaded_binding",
            "loaded launchd profile differs from approved binding",
        )


class NativeServiceGroup:
    """Run on the target host; never treat SSH or a user unit as system authority."""

    def __init__(self, spec: dict, changes: list, runner=_run):
        self.kind = spec["kind"]
        self.units = spec["units"]
        self.runner = runner
        self.changes = changes
        self.backups = {}
        self.candidate = False
        if self.kind not in {"launchd", "systemd-system"} or not self.units:
            raise LifecycleBlockedError(
                "service_plan", "unsupported or empty native service group"
            )
        if (self.kind == "launchd" and sys.platform != "darwin") or (
            self.kind == "systemd-system" and not sys.platform.startswith("linux")
        ):
            raise LifecycleBlockedError(
                "service_host", "service plan belongs to a different host platform"
            )
        names = [u["name"] for u in self.units]
        if len(set(names)) != len(names) or any(
            not re.fullmatch(r"[A-Za-z0-9_.@-]+", n) for n in names
        ):
            raise LifecycleBlockedError(
                "service_plan", "service names must be unique and literal"
            )
        self.definitions = {}
        for unit in self.units:
            for path, digest in unit["definitions"].items():
                p = Path(path)
                allowed = (
                    (
                        p
                        == Path.home()
                        / "Library/LaunchAgents"
                        / (unit["name"] + ".plist")
                    )
                    if self.kind == "launchd"
                    else (
                        p == Path("/etc/systemd/system") / unit["name"]
                        or (
                            p.parent
                            == Path("/etc/systemd/system") / (unit["name"] + ".d")
                            and p.suffix == ".conf"
                        )
                    )
                )
                if not allowed or p.is_symlink() or p in self.definitions:
                    raise LifecycleBlockedError(
                        "service_definition_path",
                        "service definition is outside its declared unit",
                    )
                self.definitions[p] = digest

    def _checked(self, argv):
        result = self.runner(tuple(argv))
        if result.returncode:
            raise LifecycleBlockedError(
                "service_command_failed",
                "native service operation failed; output withheld",
            )
        return result.stdout

    def _adapter(self, unit):
        unit = service_binding(unit, self.candidate)
        return LaunchdServiceAdapter(
            label=unit["name"],
            plist_path=Path.home() / "Library/LaunchAgents" / (unit["name"] + ".plist"),
            expected_program=unit["program"],
            expected_workdir=unit["workdir"],
            expected_profile=unit.get("profile"),
            uid=os.getuid(),
            runner=self.runner,
        )

    def _bindings(self):
        expected = dict(self.definitions)
        if self.candidate:
            expected.update({Path(c["installed"]): c["sha256"] for c in self.changes})
        for path, digest in expected.items():
            if (
                path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise LifecycleBlockedError(
                    "service_definition_drift", "service definition digest changed"
                )
        for unit in self.units:
            unit = service_binding(unit, self.candidate)
            program = Path(unit["program"])
            if (
                not program.is_absolute()
                or hashlib.sha256(program.read_bytes()).hexdigest()
                != unit["program_sha256"]
            ):
                raise LifecycleBlockedError(
                    "service_program_drift", "service launcher digest changed"
                )

    def _system(self, unit):
        unit = service_binding(unit, self.candidate)
        raw = self._checked([
            "systemctl",
            "show",
            unit["name"],
            "--property=ActiveState,SubState,MainPID,User,ExecStart,Environment,FragmentPath,DropInPaths,WorkingDirectory",
        ])
        values = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        definitions = {
            values["FragmentPath"],
            *shlex.split(values.get("DropInPaths", "")),
        }
        env = shlex.split(values.get("Environment", ""))
        if (
            definitions != set(unit["definitions"])
            or values["User"] != unit["account"]
            or not re.search(
                r"(?:^|[; ])path=" + re.escape(unit["program"]) + r"(?:[ ;]|$)",
                values["ExecStart"],
            )
            or values.get("WorkingDirectory") != unit["workdir"]
            or "HERMES_HOME=" + unit["profile"] not in env
        ):
            raise LifecycleBlockedError(
                "service_definition_binding",
                "system service does not match approved bindings",
            )
        return values

    def preflight(self):
        self._bindings()
        running = []
        for unit in self.units:
            if self.kind == "launchd":
                running.append(self._adapter(unit).preflight().running)
                raw = self._checked([
                    "/bin/launchctl",
                    "print",
                    f"gui/{os.getuid()}/{unit['name']}",
                ])
                validate_loaded_launchd(raw, service_binding(unit, self.candidate))
            else:
                values = self._system(unit)
                running.append(
                    values["ActiveState"] == "active"
                    and values["SubState"] == "running"
                    and int(values["MainPID"]) > 0
                )
        return ServicePreflight(all(running), "running" if all(running) else "partial")

    def pids(self):
        result = set()
        for unit in self.units:
            if self.kind == "launchd":
                raw = self._checked([
                    "/bin/launchctl",
                    "print",
                    f"gui/{os.getuid()}/{unit['name']}",
                ])
                match = re.search(r"^\s*pid = (\d+)\s*$", raw, re.M)
                if match:
                    result.add(int(match.group(1)))
            else:
                result.add(int(self._system(unit)["MainPID"]))
        return result - {0}

    def close(self):
        for unit in reversed(self.units):
            if self.kind == "launchd":
                adapter = self._adapter(unit)
                if not adapter.closed():
                    self._checked([
                        "/bin/launchctl",
                        "bootout",
                        f"gui/{os.getuid()}/{unit['name']}",
                    ])
            else:
                self._checked(["systemctl", "stop", unit["name"]])

    def closed(self):
        if self.kind == "launchd":
            return all(self._adapter(u).closed() for u in self.units)
        for unit in self.units:
            raw = self._checked([
                "systemctl",
                "show",
                unit["name"],
                "--property=ActiveState,MainPID",
            ])
            values = dict(
                line.split("=", 1) for line in raw.splitlines() if "=" in line
            )
            if values.get("ActiveState") != "inactive" or values.get("MainPID") != "0":
                return False
        return True

    def open(self):
        self._bindings()
        for unit in self.units:
            if self.kind == "launchd":
                self._adapter(unit).open()
            else:
                self._system(unit)
                self._checked(["systemctl", "start", unit["name"]])

    def validate_changes(self):
        self._bindings()
        for unit in self.units:
            candidate = service_binding(unit, True)
            prepared = Path(candidate.get("prepared_program", candidate["program"]))
            if (
                not prepared.is_absolute()
                or hashlib.sha256(prepared.read_bytes()).hexdigest()
                != candidate["program_sha256"]
            ):
                raise LifecycleBlockedError(
                    "service_candidate_program",
                    "prepared candidate launcher differs from approved digest",
                )
            if (
                candidate["name"] != unit["name"]
                or candidate["definitions"] != unit["definitions"]
            ):
                raise LifecycleBlockedError(
                    "service_candidate_scope",
                    "candidate cannot change unit identity or definition paths",
                )
        seen = set()
        for change in self.changes:
            target = Path(change["installed"])
            source = Path(change["candidate"])
            if target not in self.definitions or target in seen or source.is_symlink():
                raise LifecycleBlockedError(
                    "service_change_scope",
                    "definition change is not an existing declared unit file",
                )
            seen.add(target)
            if hashlib.sha256(source.read_bytes()).hexdigest() != change["sha256"]:
                raise LifecycleBlockedError(
                    "service_change_digest",
                    "candidate service definition digest differs",
                )
            self.backups[target] = (target.read_bytes(), target.stat())

    def persist_backups(self, directory):
        metadata = []
        for index, (path, (data, info)) in enumerate(self.backups.items()):
            backup = directory / f"definition-{index}.backup"
            backup.write_bytes(data)
            backup.chmod(0o600)
            metadata.append({
                "installed": str(path),
                "backup": backup.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "mode": info.st_mode & 0o777,
                "uid": info.st_uid,
                "gid": info.st_gid,
            })
        manifest = directory / "definitions.json"
        manifest.write_text(json.dumps(metadata, sort_keys=True) + "\n")
        manifest.chmod(0o600)

    @staticmethod
    def _replace(path, data, metadata):
        temp = path.with_name(f".{path.name}.{os.getpid()}.guarded")
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.chmod(metadata.st_mode & 0o777)
        if os.geteuid() == 0:
            os.chown(temp, metadata.st_uid, metadata.st_gid)
        os.replace(temp, path)

    def candidate_definitions(self):
        if not self.closed():
            raise LifecycleBlockedError(
                "service_change_running", "service definitions require stopped services"
            )
        self.candidate = True
        for change in self.changes:
            path = Path(change["installed"])
            data = Path(change["candidate"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != change["sha256"]:
                raise LifecycleBlockedError(
                    "service_change_digest", "candidate service definition changed"
                )
            self._replace(path, data, self.backups[path][1])
        if self.kind == "systemd-system":
            self._checked(["systemctl", "daemon-reload"])

    def restore_definitions(self):
        if not self.closed():
            raise LifecycleBlockedError(
                "service_restore_running",
                "service definition restore requires stopped services",
            )
        for path, (data, metadata) in self.backups.items():
            self._replace(path, data, metadata)
        self.candidate = False
        if self.kind == "systemd-system":
            self._checked(["systemctl", "daemon-reload"])


def observe_host(deployment, profile, started, prior_pids, spec, service):
    import psutil
    from gateway.status import looks_like_gateway_command_line

    release = runtime_root(deployment)
    service_pids = service.pids()
    observed = set()
    for item in spec["profiles"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            return None
        home = profile / relative
        state_path = home / "gateway_state.json"
        heartbeat_path = home / "state/gateway.heartbeat"
        state = json.loads(state_path.read_text())
        heartbeat = json.loads(heartbeat_path.read_text())
        pid = state["pid"]
        if (
            pid not in service_pids
            or pid in prior_pids
            or state["gateway_state"] != "running"
            or heartbeat.get("pid") != pid
            or heartbeat.get("loop_tick_socket") is not True
            or state_path.stat().st_mtime < started
            or heartbeat_path.stat().st_mtime < started
            or not -5 <= time.time() - heartbeat_path.stat().st_mtime <= 60
        ):
            return None
        if any(
            state["platforms"].get(p, {}).get("state") != "connected"
            for p in item["platforms"]
        ):
            return None
        try:
            process = psutil.Process(pid)
            environment = process.environ()
            if (
                Path(process.cwd()).resolve() != release / "source"
                or Path(process.exe()).resolve()
                != (release / "surfaces/python-runtime/bin/python").resolve(strict=True)
                or not looks_like_gateway_command_line(shlex.join(process.cmdline()))
                or not environment.get("HERMES_HOME")
                or not environment.get("PYTHONPATH")
                or Path(environment["HERMES_HOME"]).resolve() != home.resolve()
                or Path(environment["PYTHONPATH"]).resolve() != release / "source"
            ):
                return None
        except psutil.Error:
            return None
        observed.add(pid)
    for port in spec["ports"]:
        if type(port) is not int or not 1024 <= port <= 65535:
            return None
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    return observed or None

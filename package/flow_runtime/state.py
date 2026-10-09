"""Durable state, budgets and verified artifact reuse. No provider credentials."""
import contextlib
import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path


class Fault(Exception):
    def __init__(self, kind, message, evidence=None):
        super().__init__(message)
        self.kind, self.evidence = kind, evidence or {}

    def record(self):
        return {"kind": self.kind, "message": str(self), "evidence": self.evidence}


class BudgetEnd(Fault):
    def __init__(self, resource):
        super().__init__("budget", "预算已用尽：" + resource)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def finite(value, name="number"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Fault("input", "需要有限数值：" + name)
    return value


def artifact_hashes(folder, names):
    return {n: digest(Path(folder) / n) for n in names}


def artifacts_valid(folder, hashes):
    if not hashes:
        return False
    try:
        return all((Path(folder) / n).is_file() and digest(Path(folder) / n) == h
                   for n, h in hashes.items())
    except OSError:
        return False


@contextlib.contextmanager
def file_lock(path, timeout=0, tick=None):
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        start = time.monotonic()
        last = start
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if now - start >= timeout:
                    raise Fault("lock_busy", "等待锁超时", {"path": str(path)})
                if tick and now - last >= 10:
                    tick("等待仿真锁：%d秒" % (now - start))
                    last = now
                time.sleep(.2)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Store:
    def __init__(self, folder, identity, limits, resume=False):
        self.folder = Path(folder).resolve()
        self.path = self.folder / "state.json"
        self.mutex = threading.RLock()
        self.continuous = False
        self.started = time.monotonic()
        self.last_accounted = self.started
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if not resume:
                raise Fault("input", "输出目录已有状态；使用--resume或新目录")
            self.data = read(self.path)
            if self.data["identity"] != identity or self.data["limits"] != limits:
                raise Fault("input", "输入、依赖代码、路由或预算已改变；请使用新目录")
            self.data["status"] = "running"
        else:
            if resume:
                raise Fault("input", "续跑目录中没有state.json")
            self.data = {"schema": 1, "identity": identity, "limits": limits,
                         "usage": {"api_calls": 0, "simulations": 0, "repairs": 0, "seconds": 0},
                         "status": "running", "events": [], "requests": {},
                         "simulations": {}, "plans": {}, "patches": {}, "checkpoints": {}, "reservations": {}}
        self.flush()

    def _account(self):
        now = time.monotonic()
        self.data["usage"]["seconds"] += now - self.last_accounted
        self.last_accounted = now

    def flush(self):
        with self.mutex:
            self._account()
            save(self.path, self.data)

    def remaining_seconds(self):
        with self.mutex:
            self._account()
            if self.continuous:
                return 1e12
            return max(0, self.data["limits"]["seconds"] - self.data["usage"]["seconds"])

    def reserve(self, resource, operation=None):
        with self.mutex:
            self._account()
            reservation = resource + ":" + operation if operation else None
            if reservation and reservation in self.data["reservations"]:
                return
            if not self.continuous and self.data["usage"]["seconds"] >= self.data["limits"]["seconds"]:
                raise BudgetEnd("seconds")
            if not self.continuous and self.data["usage"][resource] >= self.data["limits"][resource]:
                raise BudgetEnd(resource)
            # Reserve BEFORE dispatch; an uncertain remote operation still consumes budget.
            self.data["usage"][resource] += 1
            if reservation:
                self.data["reservations"][reservation] = True
            self.flush()

    def event(self, stage, status, detail=None):
        with self.mutex:
            item = {"time": time.time(), "stage": stage, "status": status, "detail": detail or {}}
            self.data["events"].append(item)
            self.flush()
        print(stage + "：" + status, flush=True)

    def get(self, group, key):
        with self.mutex:
            return copy.deepcopy(self.data.get(group, {}).get(key))

    def put(self, group, key, value):
        with self.mutex:
            self.data.setdefault(group, {})[key] = copy.deepcopy(value)
            self.flush()

    def finish(self, status):
        with self.mutex:
            self.data["status"] = status
            self.flush()

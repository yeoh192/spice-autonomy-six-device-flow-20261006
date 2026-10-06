"""Cross-run RAW reuse with exact circuit/model/runner/parser fingerprints."""
import inspect
import shutil
import sys
from pathlib import Path
from .state import Fault, artifact_hashes, artifacts_valid, digest, fingerprint, read, save

FILES = ("model.lib", "test.cir", "test.raw", "test.log", "execution.json")


def signature(protocol, model_path, runner, circuit, transport=None, retry=0):
    code = {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")}
    backend = "live_ltspice"
    if transport is not None:
        # Offline analytic fixtures cannot seed a live simulator cache.
        try:
            backend = {"type": type(transport).__module__ + "." + type(transport).__qualname__,
                       "source": inspect.getsource(transport if inspect.isfunction(transport) else type(transport))}
        except (OSError, TypeError):
            backend = "offline_uninspectable:" + str(id(transport))
    exe = Path(runner["argv"][0])
    value = {"protocol": protocol, "model": digest(model_path), "actual_circuit": circuit,
             "runner": runner, "runner_sha256": digest(exe) if exe.is_file() else None,
             "code": code, "python": list(sys.version_info[:2]), "backend": backend, "retry": retry}
    return fingerprint(value), value


def restore(cache, key, signature_data, destination):
    folder = Path(cache) / key
    receipt = folder / "receipt.json"
    if not receipt.exists():
        return False
    record = read(receipt)
    hashes = record.get("hashes", {})
    if (record.get("signature") != signature_data or set(hashes) != set(FILES) or
            not artifacts_valid(folder, hashes) or any((folder / n).is_symlink() for n in FILES)):
        raise Fault("cache_corrupt", "跨任务仿真缓存校验失败", {"folder": str(folder)})
    from .spice import raw_data
    if not raw_data(folder / "test.raw")["complete"] or not (folder / "test.log").stat().st_size:
        raise Fault("cache_corrupt", "共享RAW或日志不完整", {"folder": str(folder)})
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(folder / name, destination / name)
    save(destination / "cache_receipt.json", {"source": str(folder), "hashes": hashes})
    return True


def publish(cache, key, signature_data, source):
    folder = Path(cache) / key
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(source / name, folder / name)
    # Receipt is the commit marker; incomplete publications never count as cache hits.
    save(folder / "receipt.json", {"signature": signature_data,
                                  "hashes": artifact_hashes(folder, FILES)})

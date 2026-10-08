#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install only the new framework files. Existing SPICE scripts are untouched."""
import argparse
import ast
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(source, repo, isolated=False):
    source, repo = source.resolve(), repo.resolve()
    if isolated and repo.exists() and any(repo.iterdir()):
        raise ValueError("隔离安装目录必须为空或尚不存在，避免覆盖其他任务")
    if not isolated and not (repo / "automation/spice").is_dir():
        raise ValueError("不是预期的SPICE项目目录")
    manifest = json.loads((source / "package_manifest.json").read_text())
    for name, expected in manifest["files"].items():
        p = source / name
        if sha(p) != expected:
            raise ValueError("安装包校验失败：" + name)
        if p.suffix == ".py":
            ast.parse(p.read_text(encoding="utf-8"), filename=name)
    if isolated:
        (repo / "automation/spice").mkdir(parents=True, exist_ok=True)
    pairs = [(source / "spice_flow.py", repo / "spice_flow.py"), (source / "six_batch.py", repo / "six_batch.py"), (source / "develop_inventory.py", repo / "develop_inventory.py")]
    pairs.append((source / "coverage_audit.py", repo / "coverage_audit.py"))
    pairs.append((source / "iterate_models.py", repo / "iterate_models.py"))
    pairs.append((source / "complete_five_tests.py", repo / "complete_five_tests.py"))
    pairs += [(p, repo / p.relative_to(source)) for p in sorted((source / "flow_runtime").rglob("*.py"))]
    pairs += [(p, repo / p.relative_to(source)) for p in sorted((source / "template_assets").rglob("*")) if p.is_file()]
    pairs += [(p, repo / p.relative_to(source)) for p in sorted((source / "test_library_assets").rglob("*")) if p.is_file()]
    pairs += [(p, repo / "docs/reference" / p.name) for p in sorted((source / "reference_docs").glob("*.md"))]
    pairs += [(p, repo / "tests" / p.name) for p in sorted((source / "tests").glob("test_flow_*.py"))]
    pairs.append((source / "README.md", repo / "docs" / "autonomy-framework.md"))
    changed = [(src, dst) for src, dst in pairs if not dst.is_file() or sha(src) != sha(dst)]
    if not changed:
        print("安装版本已一致，无需修改。")
        return
    for src, dst in changed:
        if dst.is_symlink() or any(p.is_symlink() for p in dst.parents if p != repo and repo in p.parents):
            raise ValueError("安装目标含符号链接，拒绝写入：" + str(dst.relative_to(repo)))
        if dst.exists() and not dst.is_file():
            raise ValueError("安装目标不是普通文件：" + str(dst))
    backup = repo / "runs" / ("flow_install_" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(parents=True)
    record = {"installed": [], "previous": []}
    for src, dst in changed:
        rel = dst.relative_to(repo)
        if dst.is_symlink() or any(p.is_symlink() for p in dst.parents if p != repo and repo in p.parents):
            raise ValueError("安装目标含符号链接，拒绝写入：" + str(rel))
        if dst.exists():
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, saved)
            record["previous"].append(str(rel))
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".flow-install-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(src.read_bytes())
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        record["installed"].append({"path": str(rel), "sha256": sha(dst)})
    (backup / "installation.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已安装统一入口：", repo / "spice_flow.py")
    print("已安装分类索引和原始模板：", repo / "template_assets")
    print("安装记录与备份：", backup)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--isolated", action="store_true", help="安装到新的空目录，不覆盖主项目")
    args = ap.parse_args()
    install(Path(__file__).parent, args.repo, args.isolated)

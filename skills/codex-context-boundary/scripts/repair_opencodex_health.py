#!/usr/bin/env python3
"""Check and repair the complete Codex/opencodex boundary health chain."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any


class HealthError(RuntimeError):
    pass


def run(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise HealthError(f"命令超时：{' '.join(args)}") from error


def require_ocx() -> str:
    ocx = shutil.which("ocx")
    if not ocx:
        raise HealthError("找不到 ocx；请先安装或更新 opencodex")
    return ocx


def json_command(args: list[str], *, timeout: int = 300) -> dict[str, Any]:
    result = run(args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise HealthError(f"命令失败（{' '.join(args)}）：{detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise HealthError(f"命令未返回 JSON（{' '.join(args)}）") from error
    if not isinstance(value, dict):
        raise HealthError(f"命令返回了非对象 JSON（{' '.join(args)}）")
    return value


def command(args: list[str], *, timeout: int = 300) -> str:
    result = run(args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise HealthError(f"命令失败（{' '.join(args)}）：{detail}")
    return (result.stdout or result.stderr).strip()


def boundary_script() -> Path:
    return Path(__file__).resolve().with_name("ensure_provider_boundary.py")


def boundary_check(source_root: str | None) -> dict[str, Any]:
    args = [sys.executable, str(boundary_script()), "--check"]
    if source_root:
        args.extend(["--source-root", source_root])
    result = run(args)
    stream = result.stdout if result.stdout.strip() else result.stderr
    try:
        value = json.loads(stream)
    except json.JSONDecodeError as error:
        raise HealthError("provider-boundary 检查未返回 JSON") from error
    if not isinstance(value, dict):
        raise HealthError("provider-boundary 检查返回了非对象 JSON")
    return value


def repair_boundary(source_root: str | None) -> dict[str, Any]:
    args = [sys.executable, str(boundary_script()), "--repair", "--no-restart"]
    if source_root:
        args.extend(["--source-root", source_root])
    result = run(args)
    stream = result.stdout if result.stdout.strip() else result.stderr
    try:
        value = json.loads(stream)
    except json.JSONDecodeError as error:
        raise HealthError("provider-boundary 修复未返回 JSON") from error
    if result.returncode != 0 or value.get("ok") is not True:
        raise HealthError(f"provider-boundary 修复失败：{value}")
    return value


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HealthError(f"无法读取配置 {path}") from error
    if not isinstance(value, dict):
        raise HealthError(f"配置不是 JSON 对象：{path}")
    return value


def agent_recovery_state(config_path: Path) -> dict[str, Any]:
    raw = load_json_file(config_path).get("agentTaskRecovery")
    enabled = isinstance(raw, dict) and raw.get("enabled") is True
    model = raw.get("model") if isinstance(raw, dict) and isinstance(raw.get("model"), str) else None
    return {"enabled": enabled, "model": model}


def root_provider(config_path: Path) -> tuple[str | None, dict[str, Any]]:
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HealthError(f"无法解析 Codex 配置：{config_path}") from error
    provider = parsed.get("model_provider")
    tables = parsed.get("model_providers")
    table = tables.get(provider, {}) if isinstance(provider, str) and isinstance(tables, dict) else {}
    return (provider if isinstance(provider, str) else None, table if isinstance(table, dict) else {})


def local_opencodex_table(table: dict[str, Any], port: int) -> bool:
    base = table.get("base_url")
    if not isinstance(base, str):
        return False
    return re.match(rf"^https?://(?:127\.0\.0\.1|localhost):{port}(?:/|$)", base.strip(), re.I) is not None


def remove_root_provider(config_path: Path, expected: str) -> Path:
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found = []
    in_root = True
    pattern = re.compile(r'^\s*model_provider\s*=\s*"([^"]+)"\s*(?:#.*)?(?:\r?\n)?$')
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[", line):
            in_root = False
        if not in_root:
            continue
        match = pattern.match(line)
        if match:
            found.append((index, match.group(1)))
    if len(found) != 1 or found[0][1] != expected:
        raise HealthError("根级 model_provider 不唯一或已变化；拒绝自动修改")
    backup = config_path.with_name(f"{config_path.name}.context-boundary-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(config_path, backup)
    del lines[found[0][0]]
    temporary = config_path.with_name(f".{config_path.name}.context-boundary.tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    os.chmod(temporary, config_path.stat().st_mode & 0o777)
    os.replace(temporary, config_path)
    return backup


def collect(ocx: str, source_root: str | None) -> dict[str, Any]:
    status = json_command([ocx, "status", "--json"])
    ready = json_command([ocx, "ready", "--json"])
    paths = status.get("paths") if isinstance(status.get("paths"), dict) else {}
    config_value = paths.get("config")
    if not isinstance(config_value, str):
        raise HealthError("ocx status 未报告配置路径")
    config_path = Path(config_value)
    startup = status.get("startup") if isinstance(status.get("startup"), dict) else {}
    runtime = status.get("codexRuntime") if isinstance(status.get("codexRuntime"), dict) else {}
    proxy = status.get("proxy") if isinstance(status.get("proxy"), dict) else {}
    return {
        "ok": False,
        "opencodex_version": command([ocx, "--version"]),
        "boundary": boundary_check(source_root),
        "routing": {
            "kind": startup.get("routingKind"),
            "status": startup.get("status"),
            "reboot_safe": startup.get("rebootSafe"),
            "protection": startup.get("protection"),
            "recommended_command": startup.get("recommendedCommand"),
        },
        "proxy": {
            "running": proxy.get("running"),
            "healthy": isinstance(proxy.get("health"), dict) and proxy["health"].get("ok") is True,
            "ready": ready.get("ready") is True,
        },
        "service": {
            "installed": startup.get("serviceInstalled"),
            "viable": startup.get("serviceViable"),
            "running": startup.get("serviceRunning"),
            "stale": startup.get("serviceStale"),
        },
        "runtime": {
            "path": runtime.get("path"),
            "version": runtime.get("version"),
            "warning": runtime.get("warning"),
            "newer_available": runtime.get("newerAvailable"),
        },
        "agent_task_recovery": agent_recovery_state(config_path),
        "_config_path": str(config_path),
        "_port": status.get("listen", {}).get("port") if isinstance(status.get("listen"), dict) else 10100,
    }


def finalize(report: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if report["boundary"].get("ok") is not True:
        failures.append("provider-boundary markers missing")
    if report["routing"].get("kind") != "opencodex-local":
        failures.append(f"routing is {report['routing'].get('kind')}")
    if report["routing"].get("reboot_safe") is not True:
        failures.append("reboot protection is not active")
    if not all(report["proxy"].get(key) is True for key in ("running", "healthy", "ready")):
        failures.append("proxy is not healthy and ready")
    if report["service"].get("viable") is not True or report["service"].get("running") is not True:
        failures.append("background service is not viable and running")
    if report["runtime"].get("warning") or report["runtime"].get("newer_available"):
        failures.append("Codex runtime needs attention")
    if report["agent_task_recovery"].get("enabled") is not True:
        failures.append("encrypted V2 agent-task recovery is disabled")
    report["ok"] = not failures
    report["failures"] = failures
    report.pop("_config_path", None)
    report.pop("_port", None)
    return report


def repair(ocx: str, source_root: str | None, adopt_opencodex_route: bool) -> dict[str, Any]:
    before = collect(ocx, source_root)
    actions: list[dict[str, Any]] = []
    boundary = repair_boundary(source_root)
    actions.append({"action": "provider_boundary", "state": boundary.get("state")})

    config_path = Path(before["_config_path"])
    codex_config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    routing_kind = before["routing"].get("kind")
    if routing_kind == "custom-local":
        provider, table = root_provider(codex_config_path)
        port = before.get("_port") if isinstance(before.get("_port"), int) else 10100
        belongs_to_current = bool(provider and local_opencodex_table(table, port))
        if not provider or (not belongs_to_current and not adopt_opencodex_route):
            raise HealthError(
                "检测到外部 custom-local 路由；如确认要由 opencodex 接管，请显式增加 --adopt-opencodex-route"
            )
        backup = remove_root_provider(codex_config_path, provider)
        actions.append({"action": "remove_conflicting_root_provider", "provider": provider, "backup": str(backup)})
        command([ocx, "restore", "back"])
        actions.append({"action": "restore_proxy_routing"})
    elif routing_kind == "native":
        command([ocx, "restore", "back"])
        actions.append({"action": "restore_proxy_routing"})
    elif routing_kind not in ("opencodex-local", "native"):
        raise HealthError(f"当前路由为 {routing_kind}；无法安全自动接管")

    recovery = agent_recovery_state(config_path)
    if not recovery["enabled"]:
        json_command([ocx, "config", "set", "agentTaskRecovery", '{"enabled":true}', "--json"])
        actions.append({"action": "enable_agent_task_recovery"})

    command([ocx, "service", "repair"])
    actions.append({"action": "repair_background_service"})
    command([ocx, "doctor", "--fix-codex-runtime"], timeout=600)
    actions.append({"action": "repair_codex_runtime"})
    command([ocx, "sync"], timeout=600)
    actions.append({"action": "sync_catalog", "codex_restarted": False})

    after = finalize(collect(ocx, source_root))
    after["actions"] = actions
    if after["ok"] is not True:
        raise HealthError(f"修复完成但健康检查仍失败：{after['failures']}")
    return after


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或修复 Codex/opencodex 完整上下文边界健康链")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="只读健康检查（默认）")
    action.add_argument("--repair", action="store_true", help="执行幂等修复，不重启 Codex Desktop")
    parser.add_argument("--source-root", help="显式指定 opencodex 安装根目录")
    parser.add_argument(
        "--adopt-opencodex-route",
        action="store_true",
        help="显式允许备份并移除根级 custom-local provider，让 opencodex 接管 Codex 路由",
    )
    args = parser.parse_args()
    try:
        ocx = require_ocx()
        report = repair(ocx, args.source_root, args.adopt_opencodex_route) if args.repair else finalize(collect(ocx, args.source_root))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") is True else 1
    except HealthError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

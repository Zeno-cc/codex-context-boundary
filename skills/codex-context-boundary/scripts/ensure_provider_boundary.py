#!/usr/bin/env python3
"""Install and validate the opencodex provider-boundary repair."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_CANDIDATES = (
    Path("/opt/homebrew/lib/node_modules/@bitkyc08/opencodex"),
    Path("/usr/local/lib/node_modules/@bitkyc08/opencodex"),
)
FILES = (
    Path("src/types.ts"),
    Path("src/responses/reasoning-replay-cache.ts"),
    Path("src/adapters/openai-responses.ts"),
    Path("src/server/responses/compact.ts"),
)


class RepairError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)


def source_root(explicit: str | None) -> Path:
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.extend(ROOT_CANDIDATES)
    npm = shutil.which("npm")
    if npm:
        result = run([npm, "root", "-g"])
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(Path(result.stdout.strip()) / "@bitkyc08" / "opencodex")
    for candidate in candidates:
        candidate = candidate.resolve()
        if all((candidate / path).is_file() for path in FILES):
            return candidate
    raise RepairError("找不到完整的 opencodex 源码安装；请用 --source-root 指定安装目录")


def atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        raise RepairError(f"无法定位安全补丁锚点：{label}；可能是版本不兼容")
    if count != 1:
        raise RepairError(f"补丁锚点不唯一：{label}（{count} 处）；已停止")
    return text.replace(old, new, 1)


class PatchPlan:
    """Stage every file change before committing any of them."""

    def __init__(self) -> None:
        self.updates: dict[Path, str] = {}

    def read(self, path: Path) -> str:
        if path in self.updates:
            return self.updates[path]
        return path.read_text(encoding="utf-8")

    def stage(self, path: Path, text: str) -> None:
        self.updates[path] = text

    def commit(self) -> None:
        for path, text in self.updates.items():
            atomic_write(path, text)


def patch_file(
    path: Path,
    operations: list[tuple[str, str, str]],
    plan: PatchPlan | None = None,
) -> bool:
    text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
    original = text
    for old, new, label in operations:
        if new not in text:
            text = replace_once(text, old, new, label)
    if text != original:
        if plan is not None:
            plan.stage(path, text)
        else:
            atomic_write(path, text)
        return True
    return False


def ensure_types(root: Path, plan: PatchPlan | None = None) -> bool:
    path = root / "src/types.ts"
    return patch_file(path, [(
        "  current?: Readonly<OcxReasoningReplayIdentity>;\n",
        "  current?: Readonly<OcxReasoningReplayIdentity>;\n"
        "  /** True once this client task changes its physical provider/model boundary. */\n"
        "  routeChanged?: boolean;\n",
        "OcxReasoningReplayScopeRef.routeChanged",
    )], plan)


def ensure_replay_cache(root: Path, plan: PatchPlan | None = None) -> bool:
    path = root / "src/responses/reasoning-replay-cache.ts"
    text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
    operations: list[tuple[str, str, str]] = []
    if "interface RouteBinding" not in text:
        operations.append((
            "interface CacheEntry {\n  text: string;\n  bytes: number;\n  at: number;\n}\n\n",
            "interface CacheEntry {\n  text: string;\n  bytes: number;\n  at: number;\n}\n\n"
            "interface RouteBinding {\n"
            "  identity: Readonly<OcxReasoningReplayIdentity>;\n"
            "  at: number;\n"
            "}\n\n",
            "RouteBinding interface",
        ))
    if "const MAX_ROUTE_BINDINGS = 1024;" not in text:
        operations.append((
            "const TTL_MS = 60 * 60 * 1000;\n",
            "const TTL_MS = 60 * 60 * 1000;\nconst MAX_ROUTE_BINDINGS = 1024;\n",
            "MAX_ROUTE_BINDINGS",
        ))
    if "const routeBindings = new Map<string, RouteBinding>();" not in text:
        operations.append((
            "const entries = new Map<string, CacheEntry>();\n",
            "const entries = new Map<string, CacheEntry>();\n"
            "// The client task id is stable across model switches, so keep the last physical\n"
            "// route long enough to recognize a provider boundary on the next request. This\n"
            "// is intentionally bounded like the reasoning cache itself; it is routing\n"
            "// provenance, not conversation content.\n"
            "const routeBindings = new Map<string, RouteBinding>();\n",
            "routeBindings map",
        ))
    if "function sameIdentity(" not in text:
        operations.append((
            "}\n\nfunction processLocalIdentity(domain: string, material: string): string {\n",
            "}\n\n"
            "function sameIdentity(\n"
            "  left: Readonly<OcxReasoningReplayIdentity> | undefined,\n"
            "  right: Readonly<OcxReasoningReplayIdentity> | undefined,\n"
            "): boolean {\n"
            "  return !!left && !!right\n"
            "    && left.providerName === right.providerName\n"
            "    && left.providerDestinationIdentity === right.providerDestinationIdentity\n"
            "    && left.adapterName === right.adapterName\n"
            "    && left.modelId === right.modelId\n"
            "    && left.credentialIdentity === right.credentialIdentity;\n"
            "}\n\nfunction processLocalIdentity(domain: string, material: string): string {\n",
            "sameIdentity helper",
        ))
    if "scope.routeChanged = true;" not in text:
        operations.append((
            "  scope.current = { ...identity };\n}\n",
            "  const at = now();\n"
            "  const previous = routeBindings.get(scope.clientThreadId);\n"
            "  if (previous && at - previous.at < TTL_MS && !sameIdentity(previous.identity, identity)) {\n"
            "    scope.routeChanged = true;\n"
            "  }\n"
            "  routeBindings.set(scope.clientThreadId, { identity: { ...identity }, at });\n"
            "  for (const [threadId, binding] of routeBindings) {\n"
            "    if (at - binding.at >= TTL_MS) routeBindings.delete(threadId);\n"
            "  }\n"
            "  while (routeBindings.size > MAX_ROUTE_BINDINGS) {\n"
            "    let oldestThreadId: string | undefined;\n"
            "    let oldestAt = Infinity;\n"
            "    for (const [threadId, binding] of routeBindings) {\n"
            "      if (binding.at < oldestAt) {\n"
            "        oldestAt = binding.at;\n"
            "        oldestThreadId = threadId;\n"
            "      }\n"
            "    }\n"
            "    if (oldestThreadId === undefined) break;\n"
            "    routeBindings.delete(oldestThreadId);\n"
            "  }\n"
            "  scope.current = { ...identity };\n}\n",
            "route-change binding",
        ))
    if "routeBindings.clear();" not in text:
        operations.append((
            "export function clearReasoningReplayCacheForTests(clock?: (() => number) | null): void {\n  entries.clear();\n",
            "export function clearReasoningReplayCacheForTests(clock?: (() => number) | null): void {\n  entries.clear();\n  routeBindings.clear();\n",
            "route binding test reset",
        ))
    return patch_file(path, operations, plan)


STRIP_FOREIGN = '''/**\n * Keep native ChatGPT reasoning only when it has the native Fernet envelope\n * shape. Routed providers use the same Responses slot for unrelated opaque\n * payloads, which the native backend cannot decrypt after a model switch.\n */\nexport function stripForeignReasoningInputItems(body: unknown): unknown {\n  if (!isPlainObject(body) || !Array.isArray(body.input)) return body;\n  const input = body.input.filter(item => {\n    if (!isPlainObject(item)) return true;\n    const isCompaction = item.type === "compaction"\n      || item.type === "compaction_summary"\n      || item.type === "context_compaction";\n    if (isCompaction) {\n      const encrypted = item.encrypted_content;\n      return typeof encrypted !== "string"\n        || encrypted.startsWith("ocx1:")\n        || /^gAAAA[A-Za-z0-9_-]+={0,2}$/.test(encrypted);\n    }\n    if (item.type !== "reasoning") return true;\n    const encrypted = item.encrypted_content;\n    return typeof encrypted !== "string"\n      || /^gAAAA[A-Za-z0-9_-]+={0,2}$/.test(encrypted);\n  });\n  return input.length === body.input.length ? body : { ...body, input };\n}\n'''
STRIP_ROUTED = '''/**\n * Routed providers must not receive opaque reasoning minted by a different\n * physical route. Native OpenAI ciphertext is recognizable even after a proxy\n * restart; the routeChanged flag covers arbitrary provider-specific ciphertext\n * (for example Grok -> CII) while preserving same-provider continuations.\n */\nexport function stripRoutedForeignReasoningInputItems(\n  body: unknown,\n  routeChanged = false,\n): unknown {\n  if (!isPlainObject(body) || !Array.isArray(body.input)) return body;\n  const input = body.input.filter(item => {\n    if (!isPlainObject(item)) return true;\n    const isCompaction = item.type === "compaction"\n      || item.type === "compaction_summary"\n      || item.type === "context_compaction";\n    if (isCompaction) {\n      const encrypted = item.encrypted_content;\n      if (typeof encrypted !== "string" || encrypted.startsWith("ocx1:")) return true;\n      return !routeChanged && !/^gAAAA[A-Za-z0-9_-]+={0,2}$/.test(encrypted);\n    }\n    if (item.type !== "reasoning") return true;\n    const encrypted = item.encrypted_content;\n    if (typeof encrypted !== "string") return true;\n    return !routeChanged && !/^gAAAA[A-Za-z0-9_-]+={0,2}$/.test(encrypted);\n  });\n  return input.length === body.input.length ? body : { ...body, input };\n}\n'''


# Keep a visible-content-only normalizer so fresh and upgraded installs
# converge on one safe implementation.
NORMALIZE_ROUTED_AGENT_MESSAGES = """/**
 * `agent_message` is a Codex Desktop collaboration item, not an OpenAI
 * Responses input type understood by third-party compatible providers. Routed
 * providers therefore receive it as an ordinary user turn, just as the parsed
 * conversation view does. Keep this out of the native OpenAI forwarding path,
 * where the desktop protocol remains supported end-to-end.
 */
export function normalizeRoutedAgentMessages(body: unknown): unknown {
  if (!isPlainObject(body) || !Array.isArray(body.input)) return body;

  let changed = false;
  const input = body.input.map(item => {
    if (!isPlainObject(item) || item.type !== "agent_message") return item;
    changed = true;
    const content = Array.isArray(item.content)
      ? item.content.filter(part => {
        if (!isPlainObject(part)) return true;
        return part.type !== "encrypted_content" && typeof part.encrypted_content !== "string";
      })
      : [];
    return {
      type: "message",
      role: "user",
      content: content.length > 0
        ? content
        : [{ type: "input_text", text: "(sub-agent message received)" }],
    };
  });

  return changed ? { ...body, input } : body;
}
"""


def ensure_routed_agent_message_normalization(root: Path, plan: PatchPlan | None = None) -> bool:
    path = root / "src/adapters/openai-responses.ts"
    text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
    operations: list[tuple[str, str, str]] = []
    normalizer = globals()["NORMALIZE_ROUTED_AGENT_MESSAGES"].replace("\\n", "\n")
    if "export function normalizeRoutedAgentMessages" not in text:
        anchor = "\n/**\n * Strip unsupported `reasoning` sub-parameters for native slugs that reject them (e.g. Spark).\n"
        operations.append((anchor, "\n" + normalizer + anchor, "routed agent-message normalizer"))
    elif 'part.type !== "encrypted_content"' not in text:
        start = text.find("/**\n * `agent_message` is a Codex Desktop collaboration item")
        end = text.find("\n/**\n * Strip unsupported `reasoning` sub-parameters", start)
        if start < 0 or end < 0:
            raise RepairError("无法定位 agent_message 过滤区；可能是版本不兼容")
        operations.append((text[start:end], normalizer.rstrip("\n"), "agent-message encrypted-content sanitizer"))
    if "const routedBody = isCanonicalOpenAiForwardProvider(provider)" not in text:
        old = '''      const sanitizedBody = normalizeToolSchemas(stripSparkCompatibility(stripUnsupportedReasoningParams(stripItemIdsWhenUnstored(stripInvalidItemIds(stripUnsupportedHostedTools(sanitizeReasoningInputContent(scrubOcxCompactionItems(outBody), { preserveRawReasoningContent: provider.preserveResponsesReasoningContent === true })))))));\n'''
        new = '''      const routedBody = isCanonicalOpenAiForwardProvider(provider)\n        ? outBody\n        : normalizeRoutedAgentMessages(outBody);\n      const sanitizedBody = normalizeToolSchemas(stripSparkCompatibility(stripUnsupportedReasoningParams(stripItemIdsWhenUnstored(stripInvalidItemIds(stripUnsupportedHostedTools(sanitizeReasoningInputContent(scrubOcxCompactionItems(routedBody), { preserveRawReasoningContent: provider.preserveResponsesReasoningContent === true })))))));\n'''
        operations.append((old.replace("\\n", "\n"), new.replace("\\n", "\n"), "routed agent-message dispatch"))
    return patch_file(path, operations, plan)


def ensure_adapter_from_base(
    path: Path,
    text: str,
    foreign_template: str,
    routed_template: str,
    plan: PatchPlan | None = None,
) -> bool:
    strip_reasoning = '''/**\n * Reasoning ciphertext is provider-scoped replay state. A compaction request can\n * summarize the visible conversation without replaying those opaque items, and\n * dropping them avoids sending ciphertext minted by a different routed provider\n * to the native ChatGPT compaction endpoint.\n */\nexport function stripReasoningInputItems(body: unknown): unknown {\n  if (!isPlainObject(body) || !Array.isArray(body.input)) return body;\n  const input = body.input.filter(item => !isPlainObject(item) || item.type !== "reasoning");\n  return input.length === body.input.length ? body : { ...body, input };\n}\n'''
    strip_reasoning_template = strip_reasoning.replace("\\n", "\n")
    operations: list[tuple[str, str, str]] = []
    if "export function stripReasoningInputItems" not in text:
        anchor = '  return changed ? { ...raw, input } : body;\n}\n'
        additions = anchor + "\n" + strip_reasoning_template + "\n" + foreign_template + "\n" + routed_template + "\n"
        operations.append((anchor, additions, "reasoning/filter base insertion"))
    else:
        foreign_start = text.find("/**\n * Keep native ChatGPT reasoning")
        foreign_end = text.find("\nfunction stripUnsupportedReasoningSummaryDelivery", foreign_start)
        if foreign_start < 0 or foreign_end < 0:
            reasoning_start = text.find("/**\n * Reasoning ciphertext is provider-scoped replay state.")
            reasoning_end = text.find("\nfunction stripUnsupportedReasoningSummaryDelivery", reasoning_start)
            if reasoning_start < 0 or reasoning_end < 0:
                raise RepairError("无法定位 reasoning 过滤区；可能是版本不兼容")
            reasoning_block = text[reasoning_start:reasoning_end]
            operations.append((reasoning_block, reasoning_block + "\n" + foreign_template, "native foreign-reasoning filter"))
            foreign_block = foreign_template
        else:
            foreign_block = text[foreign_start:foreign_end]
            if 'item.type === "compaction_summary"' not in foreign_block:
                operations.append((foreign_block, foreign_template, "native compaction boundary upgrade"))
                foreign_block = foreign_template
        if "export function stripRoutedForeignReasoningInputItems" not in text:
            operations.append((foreign_block, foreign_block + "\n" + routed_template, "routed foreign-reasoning filter"))
    if "parsed._reasoningReplayScope?.routeChanged === true" not in text:
        old = '''      if (!isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = promoteClientLoadedTools(outBody);\n      }\n'''
        new = '''      if (isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = stripForeignReasoningInputItems(outBody);\n      } else {\n        outBody = stripRoutedForeignReasoningInputItems(\n          outBody,\n          parsed._reasoningReplayScope?.routeChanged === true,\n        );\n      }\n      if (!isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = promoteClientLoadedTools(outBody);\n      }\n'''
        operations.append((old.replace("\\n", "\n"), new.replace("\\n", "\n"), "passthrough provider-boundary dispatch"))
    return patch_file(path, operations, plan)



def ensure_adapter(root: Path, plan: PatchPlan | None = None) -> bool:
    path = root / "src/adapters/openai-responses.ts"
    text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
    operations: list[tuple[str, str, str]] = []
    foreign_template = globals()["STRIP_FOREIGN"].replace("\\n", "\n")
    routed_template = globals()["STRIP_ROUTED"].replace("\\n", "\n")
    if (
        "export function stripReasoningInputItems" not in text
        or "export function stripForeignReasoningInputItems" not in text
        or "export function stripRoutedForeignReasoningInputItems" not in text
        or 'item.type === "compaction_summary"' not in text
    ):
        return ensure_adapter_from_base(path, text, foreign_template, routed_template, plan)
    if "parsed._reasoningReplayScope?.routeChanged === true" not in text:
        old = '''      if (!isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = promoteClientLoadedTools(outBody);\n      }\n'''
        new = '''      if (isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = stripForeignReasoningInputItems(outBody);\n      } else {\n        outBody = stripRoutedForeignReasoningInputItems(\n          outBody,\n          parsed._reasoningReplayScope?.routeChanged === true,\n        );\n      }\n      if (!isCanonicalOpenAiForwardProvider(provider)) {\n        outBody = promoteClientLoadedTools(outBody);\n      }\n'''
        operations.append((old.replace("\\n", "\n"), new.replace("\\n", "\n"), "passthrough provider-boundary dispatch"))
    return patch_file(path, operations, plan)

def ensure_compact(root: Path, plan: PatchPlan | None = None) -> bool:
    path = root / "src/server/responses/compact.ts"
    text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
    operations: list[tuple[str, str, str]] = []
    if "stripReasoningInputItems" not in text:
        operations.append((
            'import { FORWARD_HEADERS, sanitizeReasoningInputContent } from "../../adapters/openai-responses";',
            'import { FORWARD_HEADERS, sanitizeReasoningInputContent, stripReasoningInputItems } from "../../adapters/openai-responses";',
            "compact adapter import",
        ))
    if "const compactBody = stripReasoningInputItems(" not in text:
        operations.append((
            "    const compactBody = sanitizeReasoningInputContent(compactBodyRaw) as typeof compactBodyRaw;\n",
            "    const compactBody = stripReasoningInputItems(\n"
            "      sanitizeReasoningInputContent(compactBodyRaw),\n"
            "    ) as typeof compactBodyRaw;\n",
            "native compact reasoning boundary",
        ))
    return patch_file(path, operations, plan)


def missing_markers(root: Path, plan: PatchPlan | None = None) -> list[str]:
    checks = {
        "src/types.ts": ("routeChanged?: boolean;",),
        "src/responses/reasoning-replay-cache.ts": (
            "const MAX_ROUTE_BINDINGS = 1024;",
            "const routeBindings = new Map<string, RouteBinding>();",
            "function sameIdentity(",
            "scope.routeChanged = true;",
            "routeBindings.clear();",
        ),
        "src/adapters/openai-responses.ts": (
            "export function stripForeignReasoningInputItems",
            "export function stripRoutedForeignReasoningInputItems",
            "export function normalizeRoutedAgentMessages",
            'part.type !== "encrypted_content"',
            "parsed._reasoningReplayScope?.routeChanged === true",
            "const routedBody = isCanonicalOpenAiForwardProvider(provider)",
            'item.type === "compaction_summary"',
        ),
        "src/server/responses/compact.ts": ("stripReasoningInputItems(",),
    }
    missing: list[str] = []
    for relative, needles in checks.items():
        path = root / relative
        text = plan.read(path) if plan is not None else path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                missing.append(f"{relative}: {needle}")
    return missing


def binary(name: str, extras: tuple[Path, ...]) -> str | None:
    for candidate in extras:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def compile_sources(root: Path) -> None:
    bun = binary("bun", (
        root / "node_modules/bun/bin/bun.exe",
        Path("/opt/homebrew/lib/node_modules/@bitkyc08/opencodex/node_modules/bun/bin/bun.exe"),
        Path("/usr/local/lib/node_modules/@bitkyc08/opencodex/node_modules/bun/bin/bun.exe"),
        Path("/opt/homebrew/bin/bun"),
        Path("/usr/local/bin/bun"),
    ))
    if not bun:
        raise RepairError("找不到 Bun，无法验证代理源码")
    with tempfile.TemporaryDirectory(prefix="codex-context-boundary-") as output:
        result = run([
            bun, "build",
            str(root / "src/server/responses/compact.ts"),
            str(root / "src/adapters/openai-responses.ts"),
            str(root / "src/responses/reasoning-replay-cache.ts"),
            "--outdir", output, "--target", "bun",
        ], root)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise RepairError(f"Bun 解析/打包失败，代理未重启：{detail}")


def restart_health(root: Path) -> dict[str, object]:
    expected = root / "bin/ocx.mjs"
    candidates = (expected, Path("/opt/homebrew/bin/ocx"), Path("/usr/local/bin/ocx"))
    ocx = None
    expected_resolved = expected.resolve()
    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            if candidate.resolve() == expected_resolved:
                ocx = str(candidate)
                break
        except OSError:
            continue
    if not ocx:
        raise RepairError("找不到与 --source-root 对应的 ocx；源码已更新但无法重启代理")
    restart = run([ocx, "restart"])
    if restart.returncode != 0:
        detail = (restart.stderr or restart.stdout).strip()[-2000:]
        raise RepairError(f"ocx restart 失败：{detail}")
    health = run([ocx, "health", "--json"])
    if health.returncode != 0:
        detail = (health.stderr or health.stdout).strip()[-2000:]
        raise RepairError(f"代理健康检查失败：{detail}")
    try:
        payload = json.loads(health.stdout)
    except json.JSONDecodeError as error:
        raise RepairError("代理健康检查未返回 JSON") from error
    if payload.get("ok") is not True:
        raise RepairError(f"代理健康检查返回异常状态：{payload}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或修复 opencodex provider-boundary handling")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="只检查，不写文件、不重启")
    action.add_argument("--repair", action="store_true", help="幂等应用修复并重启代理（默认）")
    parser.add_argument("--source-root", help="显式指定 opencodex 安装根目录")
    parser.add_argument("--no-restart", action="store_true", help="修复并打包，但不重启代理")
    args = parser.parse_args()
    try:
        root = source_root(args.source_root)
        missing = missing_markers(root)
        if args.check:
            result = {"ok": not missing, "source_root": str(root), "missing": missing}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if not missing else 1
        plan = PatchPlan()
        changed = False
        changed |= ensure_types(root, plan)
        changed |= ensure_replay_cache(root, plan)
        changed |= ensure_adapter(root, plan)
        changed |= ensure_routed_agent_message_normalization(root, plan)
        changed |= ensure_compact(root, plan)
        missing = missing_markers(root, plan)
        if missing:
            raise RepairError("修复后仍缺少边界标记：" + "; ".join(missing))
        plan.commit()
        missing = missing_markers(root)
        if missing:
            raise RepairError("提交修复后仍缺少边界标记：" + "; ".join(missing))
        compile_sources(root)
        health = None if args.no_restart else restart_health(root)
        result = {
            "ok": True,
            "source_root": str(root),
            "changed": changed,
            "state": "repaired" if changed else "already-installed",
        }
        if health is not None:
            result["health"] = health
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RepairError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

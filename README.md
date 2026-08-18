# Codex Context Boundary | opencodex 上下文边界修复 Skill

> Codex/opencodex skill for encrypted reasoning, context compaction, `invalid_encrypted_content`, model switching, and routed-provider failures.

一个面向 Codex 和 opencodex provider proxy 的上下文边界修复方案，专门处理模型、供应商、适配器、目标地址或凭据切换后，隐藏 reasoning 与 compaction 状态被错误复用的问题。

它解决的不是普通对话文本丢失，而是“看得见的对话还在，但看不见的加密推理状态不属于当前 provider”这一类协议问题。

本方案来自使用 [opencodex](https://github.com/lidge-jun/opencodex) 的实际场景。opencodex 是一个本地 provider proxy，把 Codex 的 Responses API 转换到不同模型和供应商；正因为它让 Codex 可以接入更多模型，provider 之间的隐藏状态边界也需要被明确处理。

## 这是什么

Codex 的 Responses 历史里可能包含以下几类内容：

- 用户消息和可见的 assistant 消息；
- tool call 与 tool result；
- provider 生成的隐藏 reasoning；
- 上下文压缩产生的 compaction 状态。

其中，`reasoning.encrypted_content` 和外来的 opaque compaction ciphertext 属于 provider 作用域内的 replay state。它们是给同一个物理 provider、账号、模型和适配器继续使用的协议状态，不是可以在不同 provider 之间搬运的普通文本。opencodex 自己生成的 `ocx1:` compaction envelope 是透明摘要，代理可以先解码它；需要隔离的是上一条物理路线产生、当前路线无法验证的外来 opaque 状态。

当 Codex 从 OpenAI 切换到 Grok、CII 或其他 routed provider，仍把上一条路线产生的 opaque 状态原样发送给新路线时，新 provider 或原生 OpenAI 后端可能无法解密、解析或接受这些内容，于是出现：

```text
invalid_encrypted_content
Encrypted content could not be decrypted or parsed
Upstream request failed
```

## 为什么会发生

模型切换通常只改变了下一次请求的路由，但历史输入里可能还保留上一条路由产生的隐藏状态。下面这条链路就会触发问题：

```text
OpenAI reasoning state
        │
        ├── 切换模型或 provider
        │
        └── 原样回放到 Grok / CII / 新账号
                    │
                    └── 无法解密或不接受该 opaque 状态
```

子 agent 场景也可能触发类似问题。Codex Desktop 的 `agent_message` 是协作协议中的内部 item，第三方 OpenAI-compatible provider 不一定认识这种类型；如果直接转发，也可能在子 agent 回复后出现 400、502 或重连失败。

## 怎么做

修复分为两层：运行时代理负责真正的边界隔离，Codex skill 负责检查、修复和升级后的恢复。

### 1. 在路由边界记录物理身份

代理按 client thread 记录最近一次路线的身份：

- provider；
- destination；
- adapter；
- model；
- credential。

如果同一任务的物理路线发生变化，并且代理仍保留上一条路线的绑定记录，就把这次请求标记为新的 replay boundary。当前实现的绑定记录有 1 小时 TTL 和 1024 条上限，因此这是一个有界的检测窗口，不是对任意时间跨度的绝对保证。隐藏 reasoning 的连续性会在识别到边界时重置，但可见对话仍然保留。

### 2. 在请求发出前过滤外来 opaque 状态

- 原生 OpenAI 路径只保留原生可识别的 reasoning 和 compaction 形态，并在必要时先解码 opencodex 自己生成的 `ocx1:` 摘要；
- routed provider 路径不接收其他物理路线产生的 opaque reasoning 或 compaction，但可以处理可解码的 `ocx1:` 摘要；
- `/responses/compact` 直连转发前也执行 reasoning 清理；
- Codex Desktop 的 `agent_message` 在 routed provider 路径上转换为普通 user message，并过滤其中嵌套的 `encrypted_content`；
- 边界过滤阶段不主动删除用户消息、可见 assistant 内容和工具结果；adapter 仍可能为上游兼容性重写 tool schema、item ID 或 custom tool 结构，正式 compaction 也可能按协议用摘要替换部分历史。

### 3. 用幂等脚本恢复安装状态

补丁写入阶段只修改四个明确的 opencodex 源文件，流程是：

1. 定位本地 opencodex 安装；
2. 检查源码锚点与结构；
3. 先在内存中完成四个文件的全量预检，只在锚点唯一且结构兼容时一次性写入补丁；
4. 编译受影响的 Bun 入口；
5. 重启代理并检查健康状态；
6. 如果已经安装，则返回 `already-installed`，不重复修改。

脚本不会通过猜测文本、重写历史会话或删除错误记录来“掩盖”问题。结构不兼容时会停止并报告，且不会提交前序文件；重启步骤仍会产生正常的运行时状态变化。

## 能解决什么

这个方案针对以下问题：

- OpenAI、Grok、CII 或其他 provider 之间切换后出现 `invalid_encrypted_content`；
- 上下文压缩提示 encrypted content 无法解密或解析；
- 因不兼容 `agent_message` 或外来 opaque state 而出现、且错误证据指向该原因的 400/502/重连失败；
- routed provider 收到不认识的 Codex `agent_message`；
- opencodex 升级或重装后原有 provider-boundary 补丁被覆盖；
- 在客户端重新发送完整可见输入、或代理成功展开 continuation history 时，保留可见上下文并安全地重新开始隐藏推理连续性。

## 不能解决什么

以下情况不属于加密边界问题，不能仅靠这个方案解决：

- provider 自身容量不足、限流、网络中断、超时或 502/503；
- Codex 请求绕过本地 opencodex 代理，直接访问 provider；
- 代理进程没有运行，或 Codex 的 `openai_base_url` 已指向其他地址；
- provider 新增了完全不同且尚未支持的协议；
- 已经被用户手动删除的历史内容；
- opencodex continuation state 已过期或被容量上限淘汰，而当前请求又没有携带完整 history；这时无法保证可见上下文或 tool pairing 完整；
- 需要恢复上一 provider 隐藏 reasoning 的场景。

看到 502 或 503 时，先区分它是上游可用性错误，还是错误内容中同时包含了新的 `invalid_encrypted_content`。不要因为一次 502 就反复重启代理。

## 使用方式

### 上游依赖

本方案针对 npm 包 `@bitkyc08/opencodex` 以及它启动的本地 `ocx` 代理。安装和使用前请先阅读[上游仓库](https://github.com/lidge-jun/opencodex)的文档；常见安装方式是：

```bash
npm install -g @bitkyc08/opencodex
ocx start
```

如果本机已经安装并运行 opencodex，不需要重复安装；升级后应重新运行本方案的 `--check`。

### 安装本 skill

仓库现在包含可直接复制的 `skills/codex-context-boundary/` skill，以及显式命令 prompt。clone 后执行：

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME/skills" "$CODEX_HOME/prompts"
cp -R skills/codex-context-boundary "$CODEX_HOME/skills/"
cp prompts/context-boundary-repair.md "$CODEX_HOME/prompts/"
```

重启 Codex 后，使用 `$codex-context-boundary`；如果同时安装了 prompt，也可以使用 `/prompts:context-boundary-repair` 直接执行一次性修复。

### 在 Codex 中显式调用

在 Codex composer 中使用：

```text
$codex-context-boundary
```

如果使用自定义 prompt 命令，则使用：

```text
/prompts:context-boundary-repair
```

自定义 prompt 会让 Codex 执行本地确定性修复脚本。它是命令入口，不是对话历史修改器。

### 在终端中检查

下面的路径假设 skill 已安装在 `$CODEX_HOME/skills`；如果没有设置 `CODEX_HOME`，通常使用 `~/.codex`：

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SKILL_ROOT="$CODEX_HOME/skills/codex-context-boundary"

python3 "$SKILL_ROOT/scripts/ensure_provider_boundary.py" --check
```

### 在终端中修复

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SKILL_ROOT="$CODEX_HOME/skills/codex-context-boundary"

python3 "$SKILL_ROOT/scripts/ensure_provider_boundary.py" --repair
```

如果只需要确认补丁能否安全应用而不想重启代理，可以使用：

```bash
python3 "$SKILL_ROOT/scripts/ensure_provider_boundary.py" --repair --no-restart
```

如果 opencodex 安装位置不是脚本自动发现的路径，可以显式指定：

```bash
python3 "$SKILL_ROOT/scripts/ensure_provider_boundary.py" \
  --repair \
  --source-root /absolute/path/to/opencodex
```

`--source-root` 必须与实际要重启的 `ocx` 属于同一套安装。脚本会优先使用 `<source-root>/bin/ocx.mjs`，不会用一个源码目录去修补、再重启另一套全局代理。

## 验证结果应该怎么看

健康的检查结果通常包含：

```json
{
  "ok": true,
  "missing": []
}
```

幂等修复如果没有需要写入的内容，会返回类似：

```json
{
  "ok": true,
  "changed": false,
  "state": "already-installed"
}
```

代理健康检查应返回 `ok: true`。`--check` 返回非零只表示发现缺失标记，按流程继续运行 `--repair`；如果 `--repair` 返回错误或非零退出码，应保留错误信息并停止，不要手工猜测补丁位置。skill 流程可以把这种修复失败标记为 blocked，但它不是脚本的成功状态。

## 安全边界

- 不打印 API key、OAuth token、cookie 或完整 session transcript；
- 不读取或重写 Codex session JSONL 作为第一线修复；
- 不把 encrypted payload 当作可以复制、摘要或拼接的普通文本；
- 不在未知版本上用模糊替换强行打补丁；
- 不把 skill 描述成可以监听每一次桌面模型切换的后台服务；
- 真正的强制执行点是 opencodex adapter，skill 是检查和自愈流程。

## 验证过的场景

在同一台本地代理上，使用 `gpt-5.6-luna` 和 `grok/grok-4.5` 做过一次本机 smoke test，分别验证以下两类请求：

1. 用“OpenAI 切换到 Grok 后发生上下文压缩解密失败”的自然语言描述测试自动 skill 触发；
2. 使用 `$codex-context-boundary` 测试显式 skill 调用。

这次本机测试中，两种模型都识别了相关 skill，`--check` 返回无缺失标记，幂等修复返回 `already-installed`，代理健康检查返回 `ok: true`。这是一次特定本机配置下的 smoke test，不等同于所有版本、账号或 provider 组合都已覆盖。

## 设计取舍

这个方案刻意选择“保留可见上下文，重置隐藏连续性”，因为跨 provider 复用不可验证的 opaque reasoning 比重新开始隐藏推理更容易造成请求失败。边界过滤阶段会保留可见内容和任务目标，工具协议可能会经过当前 adapter 的兼容性转换；正式 compaction 仍会按自己的合约生成摘要并替换部分历史，provider-specific 的隐藏状态则由当前路线重新生成。

## 常见问题

### 为什么不直接删除出错的 session JSONL？

因为那会丢失可见对话、工具结果和任务上下文，而且无法防止下一次模型切换再次产生同类问题。边界应该在请求转发层解决。

### 切换模型后还保留原来的对话吗？

在没有发生正式 compaction 时，且客户端已发送这些输入或代理成功展开了 continuation state，用户消息、可见回复和工具结果的语义会继续保留；只有 provider-specific 的隐藏 reasoning 和外来 compaction replay state 会被隔离，opencodex 自己可解码的 `ocx1:` 摘要会按协议转换。continuation state 有 TTL 和容量上限，过期或淘汰后不保证缺失的历史会自动恢复。正式 compaction 可能用摘要替换部分历史，这是压缩协议本身的行为。

### 这个 skill 会自动修复所有模型切换吗？

skill 的自动触发依赖 Codex 对用户请求的语义匹配，并不是桌面事件监听器。日常切换的实际隔离由代理层执行；skill 主要用于报错后的诊断、手动恢复和 opencodex 升级后的重新安装。

### opencodex 升级后怎么办？

先运行 `--check`。如果标记缺失，再运行 `--repair`。如果脚本报告版本结构不兼容，应停止并针对新版本重新定位边界，不要反复重启。

## 项目状态

这是一个针对本地 Codex/opencodex 兼容层的修复方案，不是 OpenAI 官方组件。它适用于明确经过本地代理的请求；使用前应确认代理来源、运行权限和上游 provider 配置都符合自己的环境。

## 上游项目与致谢

特别感谢 [lidge-jun](https://github.com/lidge-jun) 以及 [opencodex 项目](https://github.com/lidge-jun/opencodex) 的贡献者，提供了一个实用的本地 provider proxy，让 Codex、Claude Code、Claude Desktop 和 Grok Build 可以使用更多模型与供应商。这个工具的路由、适配器和多模型能力是本方案能够被发现和验证的基础。

本仓库包含可分发的 Codex skill（`skills/codex-context-boundary/`）和显式命令 prompt（`prompts/context-boundary-repair.md`）。本文与 skill 记录的是使用 opencodex 时发现的本地兼容层问题与修复思路，不代表 opencodex 官方实现，也不是对上游代码的替代版本。请优先阅读上游仓库的安装说明、文档、贡献指南和许可条款；opencodex 使用 MIT License，本方案对上游项目保持链接和致谢。

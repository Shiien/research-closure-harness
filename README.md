# Research Closure Harness

[English](#english) | [中文](#中文)

## English

A lightweight project-management system for PhD research. It does not ask AI to
choose your research direction. Instead, it requires each unit of work to close
the following loop:

**Frozen Claim → Test → Evidence → Decision → Artifact**

It supports:

- **Codex** through the repository-level `AGENTS.md`;
- **Claude Code** through `CLAUDE.md`, two skills, and an optional hook;
- **researcher self-management** through experiment, weekly, and sprint templates;
- **mechanical checks** through a dependency-free Python CLI.

For the complete methodology, see [docs/protocol.md](docs/protocol.md).

### The claim-graph engine

The claim graph — theory layer, observation DAG, probes, and the pre-registered
resolution map — is the **engine** of the harness, not an optional add-on. A
sprint cannot be frozen, an experiment cannot be opened, and a result cannot be
recorded without it. It makes three forms of scientific reasoning explicit
and auditable:

| Mode | Direction | CLI |
|---|---|---|
| **Deduction** | theory and observation DAG → testable implications → probes | `claim_graph.py deduce` |
| **Induction** | independent closed probes → theory node with a new prediction | `claim_graph.py induce` |
| **Abduction** | anomaly against the DAG → candidate structural repairs | `claim_graph.py abduce` |

Display this map at any time with:

```bash
python tools/claim_graph.py reasoning
```

These are named transitions between theory, observations, and probes—not labels
added after the evidence is known. Their candidate sets, selections, amendments,
and decisions remain part of the audit trail.

### Core principles

1. Keep only **one active project question** at a time.
2. Keep only **one frozen sprint claim** at a time.
3. Keep only **one primary open experiment** at a time.
4. Before opening an experiment, record its hypothesis, measurement, kill criterion, and expected artifact.
5. Close every experiment with exactly one decision: `supported`, `falsified`, `inconclusive`, or `terminated`.
6. An experiment is not complete without a decision note.
7. Put new ideas in the backlog by default instead of implementing them immediately.
8. End each day with an inspectable artifact: code, a figure, a table, a proof, written text, or a documented negative result.
9. Narrowing the claim takes priority over replacing the entire research question.
10. In the final PhD stage, prioritize a complete draft over a broad, idealized theory.

### Quick start

Copy this directory into the root of a research repository, then run:

```bash
python tools/research_closure.py init
python tools/research_closure.py set-project \
  --agenda "Continual option learning" \
  --question "When can a learned representation support stable option discovery?" \
  --minimum "A complete four-to-six-page technical note"
python tools/claim_graph.py init --claim "A conditioning quantity predicts affine representation recovery under controlled excitation."
python tools/claim_graph.py add-variable --id E --name "behaviour-policy excitation" --role intervention
python tools/claim_graph.py add-variable --id K --name "log condition number" --role candidate_predictor
python tools/claim_graph.py add-variable --id R --name "affine recovery error" --role outcome
python tools/claim_graph.py add-edge --from E --to K
python tools/claim_graph.py add-edge --from K --to R
python tools/claim_graph.py add-probe --id P1 \
  --tests '{"kind":"edge","from":"K","to":"R"}' \
  --metric "spearman_rho" --prereg "rho > 0.5" --controls E
python tools/claim_graph.py add-resolution --when '{"P1":"positive"}' --then supported
python tools/claim_graph.py validate
python tools/research_closure.py start-sprint \
  --claim "A conditioning quantity predicts affine representation recovery under controlled excitation." \
  --days 14 \
  --artifact "A four-page technical note with one main figure"
python tools/research_closure.py next
```

The harness is event-driven: `next` always tells you the event it expects
(sets the project, authors the graph, freezes the sprint, opens the experiment
on the ready probe, closes it, closes the sprint), and `events` shows the log.

### Human progress tracking: the dashboard

```bash
python tools/research_closure.py dashboard
```

Renders `.research/dashboard.html` — a self-contained, offline HTML page with an
interactive DAG of the claim graph — and opens it in the browser. It shows the
theory layer (M), the observation DAG (observed/latent variables, edges,
assumed-absent ✗), and the probes (P) colour-coded by status (READY, positive,
negative, unresolved, skipped/waiting), with drag-pan and wheel-zoom. Hover a
node for its pre-registration details; click a probe for its tests, metric and
outcome. Below the graph: the resolution map with which rules currently fire,
the guard verdict, the next events, the state table and the full event log.
`guard` also prints the dashboard command whenever a claim graph exists.

### Research replay: half-finished research is readable and continuable

`tools/research_replay.py` makes any half-finished research a first-class
object — a script, a directory, or a scrubbable story:

```bash
# script -> fresh research directory (any prefix is a reproducible intermediate state)
python tools/research_replay.py run --script example/minimal_handoff/script.json --out /tmp/continued

# half-finished research directory -> script that rebuilds the same snapshot
python tools/research_replay.py export --dir /path/to/research --out /tmp/rebuild.json
python tools/research_replay.py run --script /tmp/rebuild.json --out /tmp/resumed

# script -> step-by-step replay with a scrubber over per-step dashboards
# (writes replay.html plus replay_frames/frame_*.html, one dashboard per step)
python tools/research_replay.py timeline --script example/conditioning_recovery/script.json --out /tmp/replay.html
```

The `example/` directory ships two worked examples, each as an annotated event
script plus its materialised half-finished research directory (see
[example/README.md](example/README.md)): `conditioning_recovery/` (sprint
frozen, two probes positive, an experiment open on the third) and
`minimal_handoff/` (the smallest possible mid-flight state). Use cases:
cross-session/cross-agent continuation, migration and backup recovery, teaching
and demo, and audit — the event log is the chronological source of truth.

Open an experiment:

```bash
python tools/research_closure.py new-experiment \
  --question "Does condition number predict affine recovery across excitation levels?" \
  --hypothesis "Rank correlation is positive and stable across seeds." \
  --intervention "Vary behavior-policy excitation only." \
  --measurement "Spearman correlation between log condition number and recovery error." \
  --kill "Stop using this quantity if correlation remains near zero across 30 instances and 5 seeds." \
  --artifact "results.csv and conditioning_recovery.pdf" \
  --hours 8 \
  --node P1
```

Close the experiment:

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/conditioning.csv,figures/conditioning_recovery.pdf" \
  --conclusion "Conditioning predicts recovery under controlled excitation."
```

The harness is event-driven: after each event, `next` tells you the event it
expects (open the next ready probe, or close the sprint once the resolution map
determines a verdict):

```bash
python tools/research_closure.py next
python tools/research_closure.py events
```

Close the sprint only as the frozen resolution map determines:

```bash
python tools/research_closure.py close-sprint \
  --decision advance \
  --evidence "results/conditioning.csv" \
  --conclusion "All probes supported the claim within the tested range."
```

Check whether the current project violates closure rules:

```bash
python tools/research_closure.py guard
```

### Recommended workflow

#### At the start of a session

1. Run `status` and `guard`.
2. Run `frontier` and `next` — the claim graph decides which probe may run.
3. Open an experiment on the ready probe (`new-experiment --node Pn`).
4. Ask Codex or Claude Code to read the current state before making changes.

#### Before any new experiment

Create an experiment card first, bound to a ready probe. Do not ask an agent to
“try another variant” without closing the current experiment.

#### At the end of a session

Close the experiment with the CLI and let the harness compute the verdict
(`close-experiment`); end with a real, inspectable artifact and a written decision.

#### Once per week

Complete `templates/weekly_review.md` and choose exactly one outcome:

- continue;
- narrow the claim;
- terminate;
- advance to the next claim.

“Run more experiments” is not a decision.

### Repository layout

```text
AGENTS.md                         Persistent project rules for Codex
CLAUDE.md                         Persistent project rules for Claude Code
SKILL.md                          Canonical skill: research-closure
HANDOFF_SKILL.md                  Canonical skill: research-handoff
.agents/skills/research-closure/  Repository skill for Codex
.agents/skills/research-handoff/  Repository skill for Codex
.claude/skills/research-closure/  Repository skill for Claude Code
.claude/skills/research-handoff/  Repository skill for Claude Code
.research/state.json              CLI state and event log
.research/logs/                   Sprint, experiment and decision logs
templates/                        Research planning and decision templates
tools/research_closure.py         Dependency-free lifecycle CLI (event-driven)
tools/claim_graph.py              Dependency-free claim-graph CLI (the engine)
tools/research_replay.py          Replay: script<->directory, step-by-step timeline
example/                          Annotated event scripts + materialised half-finished research
.claude/hooks/closure_guard.py    Optional mechanical gate for Claude Code
docs/protocol.md                  Complete research-closure protocol
docs/claim_graph_protocol.md      Claim-graph protocol: probes and resolution maps
examples/continual_option_learning/
                                  Worked example for a research project
```

### Strictness

The default is **graduation mode**:

- work-in-progress limit = 1;
- default sprint length = 14 days;
- the current experiment must be closed before opening another primary experiment;
- the sprint claim cannot be changed without a written decision.

Runs that differ only by seed or problem instance may share one experiment card
when they belong to the same causal comparison or diagnostic matrix.

### Questions the harness keeps asking

> Which specific uncertainty does this work reduce?
>
> Which inspectable file will it produce?
>
> Which result will make us stop?
>
> Which decision did the evidence support?

Research progress is measured by closed, inspectable uncertainties—not by the
number of ideas generated.

### Global installation

Run from the extracted directory:

```bash
./install_research_closure_global.sh
```

Or install directly from a zip file:

```bash
./install_research_closure_global.sh \
  --source ./research-closure-harness.zip
```

The default installation provides:

- Codex user skills: `~/.agents/skills/research-closure/` and `~/.agents/skills/research-handoff/`;
- Codex global rules: `${CODEX_HOME:-~/.codex}/AGENTS.md`;
- Claude Code user skills: `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/` and `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-handoff/`;
- Claude Code global rules: `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`;
- CLI: `~/.local/bin/research-closure`;
- repository initialization command: `~/.local/bin/research-closure-init`;
- claim-graph CLI: `~/.local/bin/research-closure-graph`.

Optionally install the global Claude Code `PreToolUse` hook:

```bash
./install_research_closure_global.sh --with-claude-hook
```

The installer backs up existing skills, global instruction files, and settings.
To uninstall:

```bash
./install_research_closure_global.sh --uninstall
```

---

## 中文

一套面向博士研究项目的轻量项目管理系统。它不会让 AI 替你决定研究方向，
而是要求每个研究单元闭合以下流程：

**冻结主张 → 测试 → 证据 → 决策 → 产物**

它同时支持：

- **Codex**：通过仓库根目录的 `AGENTS.md`；
- **Claude Code**：通过 `CLAUDE.md`、两个 Skill 和可选 hook；
- **研究者自我管理**：通过实验、每周和 sprint 模板；
- **机械检查**：通过无第三方依赖的 Python CLI。

完整方法说明见 [docs/protocol.md](docs/protocol.md)。

### Claim graph 引擎

Claim graph（理论层、观测 DAG、探针和预注册的 resolution map）是整个
harness 的**引擎**，而不是可选的附加功能：没有它就无法冻结 sprint、开启实验
或记录结果。它将三种科学推理作为显式、可审计的核心能力：

| 模式 | 推理方向 | CLI |
|---|---|---|
| **Deduction（演绎）** | 理论与观测 DAG → 可检验含义 → probes | `claim_graph.py deduce` |
| **Induction（归纳）** | 多个独立且已关闭的 probes → 产生新预测的理论节点 | `claim_graph.py induce` |
| **Abduction（溯因）** | 与 DAG 冲突的异常 → 候选结构修复 | `claim_graph.py abduce` |

可以随时显示这张推理地图：

```bash
python tools/claim_graph.py reasoning
```

它们是理论、观测和 probes 之间预先定义的转换，而不是看到证据后追加的标签。
候选集合、选择、修正和决策都会保留在审计轨迹中。

### 核心原则

1. 同时只能有 **一个 active project question**。
2. 同时只能有 **一个 frozen sprint claim**。
3. 同时只能有 **一个主要开放实验**。
4. 开实验前必须记录 hypothesis、measurement、kill criterion 和 expected artifact。
5. 每个实验结束时必须给出 `supported`、`falsified`、`inconclusive` 或 `terminated` 中的一个决策。
6. 没有 decision note，实验就不算完成。
7. 新想法默认进入 backlog，不立即实现。
8. 每天必须留下可检查的产物：代码、图、表、证明、文字或明确记录的负结果。
9. 降低 claim 的优先级高于更换整个研究问题。
10. 博士毕业阶段优先形成完整初稿，而不是追求宽泛且理想化的理论。

### 快速开始

将整个目录复制到研究仓库根目录，然后执行：

```bash
python tools/research_closure.py init
python tools/research_closure.py set-project \
  --agenda "持续选项学习" \
  --question "学习到的表示何时能支持稳定的选项发现？" \
  --minimum "一份完整的四至六页技术报告"
python tools/claim_graph.py init --claim "在受控激励条件下，某个条件量能够预测仿射表示恢复。"
python tools/claim_graph.py add-variable --id E --name "行为策略激励" --role intervention
python tools/claim_graph.py add-variable --id K --name "对数条件数" --role candidate_predictor
python tools/claim_graph.py add-variable --id R --name "仿射恢复误差" --role outcome
python tools/claim_graph.py add-edge --from E --to K
python tools/claim_graph.py add-edge --from K --to R
python tools/claim_graph.py add-probe --id P1 \
  --tests '{"kind":"edge","from":"K","to":"R"}' \
  --metric "spearman_rho" --prereg "rho > 0.5" --controls E
python tools/claim_graph.py add-resolution --when '{"P1":"positive"}' --then supported
python tools/claim_graph.py validate
python tools/research_closure.py start-sprint \
  --claim "在受控激励条件下，某个条件量能够预测仿射表示恢复。" \
  --days 14 \
  --artifact "包含一张主图的四页技术报告"
python tools/research_closure.py next
```

Harness 是事件驱动的：`next` 始终告诉你它期待的下一个事件（设置项目、创作
claim graph、冻结 sprint、在就绪探针上开实验、关闭实验、关闭 sprint），
`events` 显示完整事件日志。

### 人类进度追踪：dashboard

```bash
python tools/research_closure.py dashboard
```

生成 `.research/dashboard.html` —— 一个自包含、可离线打开的 HTML 页面，以
交互式 DAG 展示 claim graph 并自动在浏览器中打开。它展示理论层（M）、观测
DAG（已观测/隐变量、边、assumed-absent ✗）以及按状态着色的探针（READY、
positive、negative、unresolved、skipped/waiting），支持拖拽平移与滚轮缩放。
悬停节点查看预注册详情；点击探针查看其测试、指标与结果。图下方是：
resolution map（哪些规则当前已触发）、guard 判定、下一个事件、状态表与完整
事件日志。只要存在 claim graph，`guard` 也会打印 dashboard 命令。

### Research 回放：进行到一半的研究可读、可续、可回放

`tools/research_replay.py` 让任何"进行到一半的研究"成为一等公民——脚本、
目录或可拖动的故事：

```bash
# 脚本 -> 全新研究目录（脚本的任意前缀都是可复现的中间状态）
python tools/research_replay.py run --script example/minimal_handoff/script.json --out /tmp/continued

# 半成品研究目录 -> 重建同一快照的脚本
python tools/research_replay.py export --dir /path/to/research --out /tmp/rebuild.json
python tools/research_replay.py run --script /tmp/rebuild.json --out /tmp/resumed

# 脚本 -> 逐步回放：带进度条、逐帧展示每步的 dashboard
# （生成 replay.html 和 replay_frames/frame_*.html，每步一个 dashboard）
python tools/research_replay.py timeline --script example/conditioning_recovery/script.json --out /tmp/replay.html
```

`example/` 目录内置两个带注释的事件脚本 + 对应的物化半成品研究目录（见
[example/README.md](example/README.md)）：`conditioning_recovery/`（sprint
已冻结、两个探针 positive、第三个探针上有未关闭的实验）与 `minimal_handoff/`
（最小的中途状态）。适用场景：跨会话/跨 agent 续传、迁移与备份恢复、教学与
演示、审计——事件日志就是按时间排序的事实来源。

开启实验：

```bash
python tools/research_closure.py new-experiment \
  --question "条件数能否跨激励水平预测仿射恢复？" \
  --hypothesis "秩相关在不同随机种子下保持为正且稳定。" \
  --intervention "仅改变行为策略的激励程度。" \
  --measurement "对数条件数与恢复误差之间的 Spearman 相关。" \
  --kill "如果在 30 个实例和 5 个随机种子上相关性仍接近零，则停止使用该指标。" \
  --artifact "results.csv and conditioning_recovery.pdf" \
  --hours 8 \
  --node P1
```

关闭实验：

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/conditioning.csv,figures/conditioning_recovery.pdf" \
  --conclusion "在受控激励条件下，条件量能够预测恢复误差。"
```

Harness 是事件驱动的：每个事件之后，`next` 会告诉你它期待的下一个事件
（开启下一个就绪探针，或当 resolution map 判定后关闭 sprint）：

```bash
python tools/research_closure.py next
python tools/research_closure.py events
```

按冻结的 resolution map 关闭 sprint：

```bash
python tools/research_closure.py close-sprint \
  --decision advance \
  --evidence "results/conditioning.csv" \
  --conclusion "所有探针都在测试范围内支持了该主张。"
```

检查当前项目是否违反 closure 规则：

```bash
python tools/research_closure.py guard
```

### 推荐工作流

#### 会话开始时

1. 运行 `status` 和 `guard`。
2. 运行 `frontier` 和 `next` —— 由 claim graph 决定接下来运行哪个探针。
3. 在就绪探针上开启实验（`new-experiment --node Pn`）。
4. 让 Codex 或 Claude Code 在修改前先读取当前状态。

#### 开始任何新实验前

先创建绑定到就绪探针的 experiment card。当前实验尚未关闭时，不要直接让
agent“再试一个变体”。

#### 会话结束时

用 CLI 关闭实验并让 harness 计算结论（`close-experiment`）；结束时留下一个
真实、可检查的产物和书面决策。

#### 每周一次

填写 `templates/weekly_review.md`，并且只能选择一个结果：

- continue；
- narrow claim；
- terminate；
- advance to the next claim。

“再跑一些实验”不是一个有效决策。

### 目录说明

```text
AGENTS.md                         Codex 的持续项目规则
CLAUDE.md                         Claude Code 的持续项目规则
SKILL.md                          research-closure 技能主副本
HANDOFF_SKILL.md                  research-handoff 技能主副本
.agents/skills/research-closure/  Codex 仓库级 skill
.agents/skills/research-handoff/  Codex 仓库级 skill
.claude/skills/research-closure/  Claude Code 仓库级 skill
.claude/skills/research-handoff/  Claude Code 仓库级 skill
.research/state.json              CLI 状态与事件日志
.research/logs/                   Sprint、实验与决策日志
templates/                        研究计划与决策模板
tools/research_closure.py         无依赖生命周期 CLI（事件驱动）
tools/claim_graph.py              无依赖 claim graph CLI（引擎）
tools/research_replay.py          回放：脚本<->目录互转、逐步时间线
example/                          带注释的事件脚本 + 物化的半成品研究
.claude/hooks/closure_guard.py    Claude Code 可选机械门禁
docs/protocol.md                  完整研究 closure 协议
docs/claim_graph_protocol.md      claim graph 协议：探针集合与 resolution map
examples/continual_option_learning/
                                  研究项目示例
```

### 严格程度

默认使用 **graduation mode**：

- WIP limit = 1；
- 默认 sprint 长度 = 14 天；
- 必须先关闭当前实验，才能创建新的主要实验；
- 没有书面 decision 时，不允许修改 sprint claim。

如果多次运行仅在随机种子或问题实例上不同，并且属于同一个因果比较或诊断矩阵，
它们应共享同一个 experiment card。

### Harness 会持续追问的问题

> 当前工作减少了哪一个明确的不确定性？
>
> 它将产生哪个可检查的文件？
>
> 什么结果会让我们停止？
>
> 证据最终支持了什么决策？

研究进展不再以“产生了多少想法”计分，而以关闭了多少可检查的不确定性计分。

### 全局安装

解压后在目录内运行：

```bash
./install_research_closure_global.sh
```

或者直接让安装器读取 zip：

```bash
./install_research_closure_global.sh \
  --source ./research-closure-harness.zip
```

默认安装以下内容：

- Codex 用户 skill：`~/.agents/skills/research-closure/` 与 `~/.agents/skills/research-handoff/`；
- Codex 全局规则：`${CODEX_HOME:-~/.codex}/AGENTS.md`；
- Claude Code 用户 skill：`${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/` 与 `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-handoff/`；
- Claude Code 全局规则：`${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`；
- CLI：`~/.local/bin/research-closure`；
- 仓库初始化命令：`~/.local/bin/research-closure-init`；
- claim graph CLI：`~/.local/bin/research-closure-graph`。

可选安装 Claude Code 的全局 `PreToolUse` hook：

```bash
./install_research_closure_global.sh --with-claude-hook
```

安装器会备份已有的 skill、全局 instruction 文件和 settings。卸载命令：

```bash
./install_research_closure_global.sh --uninstall
```

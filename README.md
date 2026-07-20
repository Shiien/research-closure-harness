# Research Closure Harness

[English](#english) | [中文](#中文)

## English

A lightweight project-management system for PhD research. It does not ask AI to
choose your research direction. Instead, it requires each unit of work to close
the following loop:

**Frozen Claim → Test → Evidence → Decision → Artifact**

It supports:

- **Codex** through the repository-level `AGENTS.md`;
- **Claude Code** through `CLAUDE.md`, a skill, and an optional hook;
- **researcher self-management** through daily, experiment, weekly, and sprint templates;
- **mechanical checks** through a dependency-free Python CLI.

For the complete methodology, see [docs/protocol.md](docs/protocol.md).

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
python tools/research_closure.py start-sprint \
  --claim "A conditioning quantity predicts affine representation recovery under controlled excitation." \
  --days 14 \
  --artifact "A four-page technical note with one main figure"
python tools/research_closure.py start-day \
  --deliverable "Produce the first conditioning-vs-recovery scatter plot"
python tools/research_closure.py status
```

Open an experiment:

```bash
python tools/research_closure.py new-experiment \
  --question "Does condition number predict affine recovery across excitation levels?" \
  --hypothesis "Rank correlation is positive and stable across seeds." \
  --intervention "Vary behavior-policy excitation only." \
  --measurement "Spearman correlation between log condition number and recovery error." \
  --kill "Stop using this quantity if correlation remains near zero across 30 instances and 5 seeds." \
  --artifact "results.csv and conditioning_recovery.pdf" \
  --hours 8
```

Close the experiment:

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/conditioning.csv,figures/conditioning_recovery.pdf" \
  --conclusion "Conditioning predicts recovery under controlled excitation."
```

Close the day:

```bash
python tools/research_closure.py close-day \
  --artifact "figures/conditioning_recovery.pdf" \
  --decision "Keep the claim; next isolate estimator variance."
```

Check whether the current project violates closure rules:

```bash
python tools/research_closure.py guard
```

### Recommended workflow

#### At the start of each day

1. Run `status`.
2. Run `start-day`.
3. Record exactly one deliverable for the day.
4. Ask Codex or Claude Code to read the current state before making changes.

#### Before any new experiment

Create an experiment card first. Do not ask an agent to “try another variant”
without closing the current experiment.

#### At the end of each day

Run `close-day` and point it to a real, inspectable file.

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
SKILL.md                          Human-readable canonical skill
.agents/skills/research-closure/  Repository skill for Codex
.claude/skills/research-closure/  Repository skill for Claude Code
.research/state.json              CLI state
.research/logs/                   Daily and experiment logs
templates/                        Research planning and decision templates
tools/research_closure.py         Dependency-free CLI
.claude/hooks/closure_guard.py    Optional mechanical gate for Claude Code
docs/protocol.md                  Complete research-closure protocol
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

- Codex user skill: `~/.agents/skills/research-closure/`;
- Codex global rules: `${CODEX_HOME:-~/.codex}/AGENTS.md`;
- Claude Code user skill: `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/`;
- Claude Code global rules: `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`;
- CLI: `~/.local/bin/research-closure`;
- repository initialization command: `~/.local/bin/research-closure-init`.

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
- **Claude Code**：通过 `CLAUDE.md`、Skill 和可选 hook；
- **研究者自我管理**：通过每日、实验、每周和 sprint 模板；
- **机械检查**：通过无第三方依赖的 Python CLI。

完整方法说明见 [docs/protocol.md](docs/protocol.md)。

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
python tools/research_closure.py start-sprint \
  --claim "在受控激励条件下，某个条件量能够预测仿射表示恢复。" \
  --days 14 \
  --artifact "包含一张主图的四页技术报告"
python tools/research_closure.py start-day \
  --deliverable "生成第一张条件量与恢复误差的散点图"
python tools/research_closure.py status
```

开启实验：

```bash
python tools/research_closure.py new-experiment \
  --question "条件数能否跨激励水平预测仿射恢复？" \
  --hypothesis "秩相关在不同随机种子下保持为正且稳定。" \
  --intervention "仅改变行为策略的激励程度。" \
  --measurement "对数条件数与恢复误差之间的 Spearman 相关。" \
  --kill "如果在 30 个实例和 5 个随机种子上相关性仍接近零，则停止使用该指标。" \
  --artifact "results.csv and conditioning_recovery.pdf" \
  --hours 8
```

关闭实验：

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/conditioning.csv,figures/conditioning_recovery.pdf" \
  --conclusion "在受控激励条件下，条件量能够预测恢复误差。"
```

结束当天工作：

```bash
python tools/research_closure.py close-day \
  --artifact "figures/conditioning_recovery.pdf" \
  --decision "保留当前主张；下一步只隔离估计器方差。"
```

检查当前项目是否违反 closure 规则：

```bash
python tools/research_closure.py guard
```

### 推荐工作流

#### 每天开始时

1. 运行 `status`。
2. 运行 `start-day`。
3. 当天只记录一个 deliverable。
4. 让 Codex 或 Claude Code 在修改前先读取当前状态。

#### 开始任何新实验前

先创建 experiment card。当前实验尚未关闭时，不要直接让 agent“再试一个变体”。

#### 每天结束时

运行 `close-day`，并指向一个真实、可检查的文件。

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
SKILL.md                          技能的可读主副本
.agents/skills/research-closure/  Codex 仓库级 skill
.claude/skills/research-closure/  Claude Code 仓库级 skill
.research/state.json              CLI 状态
.research/logs/                   每日与实验日志
templates/                        研究计划与决策模板
tools/research_closure.py         无依赖 CLI
.claude/hooks/closure_guard.py    Claude Code 可选机械门禁
docs/protocol.md                  完整研究 closure 协议
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

- Codex 用户 skill：`~/.agents/skills/research-closure/`；
- Codex 全局规则：`${CODEX_HOME:-~/.codex}/AGENTS.md`；
- Claude Code 用户 skill：`${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/`；
- Claude Code 全局规则：`${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`；
- CLI：`~/.local/bin/research-closure`；
- 仓库初始化命令：`~/.local/bin/research-closure-init`。

可选安装 Claude Code 的全局 `PreToolUse` hook：

```bash
./install_research_closure_global.sh --with-claude-hook
```

安装器会备份已有的 skill、全局 instruction 文件和 settings。卸载命令：

```bash
./install_research_closure_global.sh --uninstall
```

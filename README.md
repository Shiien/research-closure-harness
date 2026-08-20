# Self-Evolved Research Harness

> **Repository repositioning.** This repository is moving from *Research Closure
> Harness* — a PhD project-management harness — toward *Self-Evolved Research
> Harness*: an auto-research system whose object of study is itself. The checked-in
> code is the mechanical closure substrate; the target architecture is summarized
> in the blueprint sections below, and the original discussion inputs are preserved
> verbatim in the appendix.

[English](#english) | [中文](#中文)

## English

> **Repo positioning.** The current implementation is a lightweight research
> closure harness. The research target of this branch is the harness itself:
> study its own graph, modify its own framework, verify changes in isolation,
> and improve its own auto-research capability. See
> [Self-evolved research blueprint](#self-evolved-research-blueprint) below.

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

### Self-evolved research blueprint

This is the design target for the `auto-research` branch. It is not a description
of what the current CLI already implements; it is the direction this repository is
being repositioned around.

#### 1. Core idea

- True auto research does not use external samples or other research projects as
  its definition of success. Those are presentation interfaces, not the thing itself.
- The research object is the framework itself: study itself, modify itself, and
  improve itself through that loop.
- Self-reference is the core mechanism: a fast version (A) and a slow version (B)
  of the same auto-research process improve each other in alternation.
- An LLM is not a random mutator. It is a **directed proposer** with local
  judgment: it can guarantee local improvement, but constraints are needed to
  prevent global degradation.

#### 2. Graph model

- Original node kinds: **Assumption**, **Inference product**, **Experiment verify**.
- Extension: **Modification/Intervention** nodes for actions that change the
  system itself.
- Two edge kinds: **dependency edges** (epistemic relations) and **modification
  edges** (causal operations).
- Node stability labels: `draft` (fast-layer product, unverified),
  `validated` (slow-layer verification passed), `deprecated` (no longer valid).

#### 3. What self-reference means in the graph

- The graph is not merely a research record; it is the **research ontology**.
  Nodes may refer to subsets or historical versions of the graph itself.
- Versioning and snapshots are required so different versions of the system can
  be compared and a modification can be judged effective or not.
- Self-referential verification must be **isolated in a sandbox**, so a
  self-modification cannot contaminate the verification standard that judges it.
- Self-reference appears as state flowing between fast and slow layers and as
  repeated modification cycles — not as a fixed cycle in a static graph.

#### 4. Verification and retention after self-modification

- **Dependency labels + invalidation closure**: from the change point, follow
  dependency edges to compute the affected set. Unaffected nodes stay by default.
- **Layered fast re-verification**:
  1. syntax/structure check against the new rule definitions;
  2. lightweight re-derivation from the same premises;
  3. re-run verification, prioritizing low-cost experiments or the critical path.
- **Trust decay**: every node's trust decays after each self-modification;
  re-verification restores it. This prevents old, long-unchecked conclusions from
  accumulating.
- **Fast/slow pre-filtering**: the fast layer performs cheap pre-screening and
  submits only the most promising candidates to the slow layer.

#### 5. Meta-goal and layered self-reference

- The only fixed meta-goal is: **improve the system's own auto-research
  capability**.
- Operational metrics, question-generation strategy, and reasoning/verification
  strategy are themselves research products of auto research and are modifiable.
- Layers:
  - **L0** — minimal meta-goal (immutable);
  - **L1** — operational metrics (modifiable, checked only indirectly against L0);
  - **L2** — question-generation strategy (modifiable);
  - **L3** — reasoning and verification strategy (modifiable);
  - **L4** — concrete research content (changes frequently).
- **Global snapshots and rollback**: periodically evaluate global state to
  prevent local improvements from accumulating into global degradation.
- **Capability self-test**: the system periodically runs simple internal tests
  to check basic capabilities and prevent self-modification from breaking them.

#### 6. Role of the LLM

- The LLM is a **proposer** and **local improver**, not a random mutator.
- Separate **soft judgment** (proposal screening) from **hard verification**
  (reproducible validation). Soft judgment cannot replace hard verification.
- Add a **critic role**: every modification proposal must be challenged before
  entering verification.
- Guard against **premature convergence**: over-confident LLM acceptance of a
  sub-optimal modification must be resisted.

#### 7. Fast/slow dynamics (A/B)

- A and B are not two agents. They are the fast and slow versions of the same
  auto-research process.
  - **A (fast layer)**: explore, mutate, propose candidates; low verification cost.
  - **B (slow layer)**: verify, consolidate, integrate; high verification cost.
- Loop: fast layer produces candidates → slow layer verifies → feedback → fast
  layer updates its strategy.
- Fast/slow rhythm, verification thresholds, and state-transition rules are also
  research content and may be modified by the system itself.

#### 8. Open questions

1. How to implement fast/slow interaction in the graph without letting the fast
   layer generate too much noise.
2. How to design the minimal immutable core (arbitration mechanism) so the system
   cannot crash or collude into degradation during self-modification.
3. How to model "define problem, decompose problem, choose principles" as graph
   nodes and include them in the reasoning loop.
4. How to compute invalidation closure efficiently and how to use sandbox
   isolation safely during self-referential verification.

#### Current repository → target mapping

| Target concept | Already present in this repository | Still to build |
|---|---|---|
| Research ontology | `claim_graph.py`: theory, observations, probes, resolution map | Modification/Intervention nodes; dependency vs modification edges; `draft`/`validated`/`deprecated` labels |
| Hard verification | Pre-registered probes, `validate`, `guard`, decision taxonomy | Sandboxed self-modification verification; critic gate |
| Versioning/snapshots | `.research/snapshots/`, dashboard history, replay script | Semantic diff between self-versions; invalidation closure over versions |
| Layered goals | `docs/protocol.md` levels 0–3 | Explicit L0–L4 self-reference layers with only L0 immutable |
| Fast/slow dynamics | Not implemented | A/B fast/slow loop, trust decay, fast pre-screening |
| Global rollback/self-test | Snapshot replay restores states | Automated global evaluation, rollback policy, capability self-test |

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
interactive DAG of the claim graph — and opens it in the browser, **opening at
the latest state**. Every mutation records a snapshot checkpoint
(`.research/snapshots/`), so the slider walks back through the research's
history with full fidelity — there is no separate "replay mode", one view does
both. It shows the theory layer (M), the observation DAG (observed/latent
variables, edges, assumed-absent ✗), and the probes (P) colour-coded by status
(READY, positive, negative, unresolved, skipped/waiting), with drag-pan and
wheel-zoom. Hover a node for its pre-registration details; click a probe for
its tests, metric and outcome. Below the graph: the resolution map with which
rules currently fire, the guard verdict, the next events, the state table and
the full event log. `guard` also prints the dashboard command whenever a claim
graph exists.

### Research replay: half-finished research is readable and continuable

`tools/research_replay.py` makes any half-finished research a first-class
object — a script, a directory, or a scrubbable story. The replay view is the
same page as the dashboard (opens at the latest state, scrub back manually):

```bash
# script -> fresh research directory (any prefix is a reproducible intermediate state)
python tools/research_replay.py run --script example/minimal_handoff/script.json --out /tmp/continued

# half-finished research directory -> script that rebuilds the same snapshot
python tools/research_replay.py export --dir /path/to/research --out /tmp/rebuild.json
python tools/research_replay.py run --script /tmp/rebuild.json --out /tmp/resumed

# script -> step-by-step view with a scrubber over per-step dashboards
# (the same unified page the dashboard renders from the snapshot journal)
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

> **仓库重新定位。** 当前实现是一套轻量 research closure harness；而本分支
> 的研究对象是 harness 自身：研究自己的图、修改自己的框架、在隔离环境中验证
> 修改，并提升自己的 auto research 能力。目标架构见下文
> [自进化 research 蓝图](#自进化-research-蓝图)，讨论中的原始输入原样保存在
> README 末尾附录。

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

### 自进化 research 蓝图

这是 `auto-research` 分支的定位目标，不是当前 CLI 已实现功能的描述；它说明
本仓库正在重新定位的方向。

#### 1. 核心思想

- 真正的 auto research 不应依赖外部样本或其他 research project 作为评判标准；
  那些只是对外展示的接口，不是 auto research 本身。
- 研究对象是框架自身：通过研究自己、修改自己来提升自己。
- 自指是核心机制：由同一 auto research 过程的快速版本（A）与慢速版本（B）
  交替提升，实现系统自我进化。
- 大模型不是随机变异器，而是具有局部判断能力的**有向提案器**；它能保障局部
  改进，但需要约束防止全局退化。

#### 2. 图结构

- 原始三类节点：**Assumption（假设）**、**Inference 产物（推理结果）**、
  **Experiment verify（实验验证）**。
- 扩展节点：**Modification/Intervention 节点**，表示对系统自身的修改动作。
- 两类边：**依赖边**（认识论关系）与**修改边**（因果操作关系）。
- 节点稳定性标签：`draft`（快速层产物，未验证）、`validated`（慢速层验证通过）、
  `deprecated`（已失效）。

#### 3. 自指在图中的含义

- 图不再只是研究记录，而是**研究本体**；节点可以引用图自身的子集或历史版本。
- 需要版本化与快照能力，以比较不同版本的自己并判断修改是否有效。
- **自指验证隔离**：在沙盒中执行修改并验证，避免验证标准被修改污染。
- 自指表现为节点在快慢层之间的状态流动与循环修改，而非固定图上的环。

#### 4. 验证与保留策略

- **依赖标签 + 失效闭包**：从变更点沿依赖边计算受影响集合；未受影响节点默认保留。
- **分层快速重验**：
  1. 语法/结构层检查（是否符合新规则定义）；
  2. 轻量重推（同一前提重新推理，比较结论）；
  3. 验证重跑（优先低成本实验或关键路径）。
- **信任度衰减**：每次自修改后所有节点信任度衰减，重新验证通过才恢复，防止
  长期未验证的旧结论累积。
- **快慢分层预筛**：快速层先做廉价预筛选，只把最有希望的候选提交给慢速层严格验证。

#### 5. 元目标与分层自指

- **唯一固定元目标**：提升自身 auto research 能力。
- 操作化指标、问题生成策略、推理验证策略都是 auto research 自身的研究产物，可修改。
- 分层：
  - **L0**：最小元目标（不可修改）；
  - **L1**：操作化指标（可修改，需通过 L0 间接检验）；
  - **L2**：问题生成策略（可修改）；
  - **L3**：推理与验证策略（可修改）；
  - **L4**：具体研究内容（经常变化）。
- **全局快照与回溯**：定期评估全局状态，防止局部改进累积成全局退化。
- **能力自检**：系统定期用简单内部测试检查自身基本能力，防止自修改破坏基础。

#### 6. 大模型的角色

- 大模型是**提案者**与**局部改进器**，不是随机变异器。
- 区分**软判断**（提案筛选）与**硬验证**（可复现验证）；软判断不能取代硬验证。
- 引入**批评者角色**：每个修改提案先被反驳，通过后才进入验证。
- 防止**过早收敛**：避免大模型过度自信而接受次优修改。

#### 7. 快慢动态（A/B 机制）

- A 和 B 不是两个 agent，而是同一 auto research 过程的快慢两个版本：
  - **A（快速层）**：探索、变异、提出候选，验证成本低；
  - **B（慢速层）**：验证、固化、整合，验证成本高。
- 交互循环：快速层产生候选 → 慢速层验证 → 反馈结果 → 更新快速层策略。
- 快慢节奏、验证阈值、状态转换规则本身也是研究内容，可被系统自身修改。

#### 8. 开放问题与后续方向

1. 如何在图中具体实现快慢层交互，避免快速层产生过多噪声。
2. 如何设计最小不可修改核心（仲裁机制），防止系统在自修改中崩溃或共谋退化。
3. 如何把“定义问题、拆解问题、选择原则”本身建模为图中节点并纳入推理循环。
4. 如何高效计算失效闭包，以及如何在自指验证中安全使用沙盒隔离。

#### 当前仓库 → 目标映射

| 目标概念 | 本仓库已有 | 尚待建设 |
|---|---|---|
| 研究本体 | `claim_graph.py`：理论、观测、探针、resolution map | Modification/Intervention 节点；依赖边/修改边；`draft`/`validated`/`deprecated` 标签 |
| 硬验证 | 预注册探针、`validate`、`guard`、决策分类 | 沙盒中的自修改验证；批评者门禁 |
| 版本化与快照 | `.research/snapshots/`、dashboard 历史、replay 脚本 | 自身版本之间的语义 diff；跨版本失效闭包 |
| 分层目标 | `docs/protocol.md` 的 level 0–3 | 显式 L0–L4 自指分层，且只有 L0 不可修改 |
| 快慢动态 | 尚未实现 | A/B 快慢循环、信任度衰减、快速层预筛选 |
| 全局回溯与自检 | 快照 replay 可恢复状态 | 自动全局评估、回溯策略、能力自检 |

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
交互式 DAG 展示 claim graph 并自动在浏览器中打开，**默认展示最新状态**。
每次变更都会在 `.research/snapshots/` 记录检查点，因此滑块可以带着完整
保真度往回翻看研究历史——**没有单独的"回放模式"，一个视图两者兼做**。
它展示理论层（M）、观测 DAG（已观测/隐变量、边、assumed-absent ✗）以及按
状态着色的探针（READY、positive、negative、unresolved、skipped/waiting），
支持拖拽平移与滚轮缩放。悬停节点查看预注册详情；点击探针查看其测试、指标
与结果。图下方是：resolution map（哪些规则当前已触发）、guard 判定、下一
个事件、状态表与完整事件日志。只要存在 claim graph，`guard` 也会打印
dashboard 命令。

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
# （单文件自包含页面，每步的 dashboard 直接挂载在页面内）
python tools/research_replay.py timeline --script example/conditioning_recovery/script.json --out /tmp/replay.html
```

`example/` 目录内置两个带注释的事件脚本 + 对应的物化半成品研究目录（见
[example/README.md](example/README.md)）：`conditioning_recovery/`（sprint
已冻结、两个探针 positive、第三个探针上有未关闭的实验）与 `minimal_handoff/`
（最小的中途状态）。回放视图与 dashboard 是同一个页面（默认最新、可手动回拉）。
适用场景：跨会话/跨 agent 续传、迁移与备份恢复、教学与演示、审计——事件日志
就是按时间排序的事实来源。

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


---

## 附录：用户原始输入

以下为用户在讨论中发出的所有消息，按时间顺序原样保存。

> **1.** 记录一下我对于self evolved research harness的思考

> **2.** 很多harness会使用其他的样本和别的research project, 来作为这个auto research做得好和不好的评判，但我认为这个只是给别人展示的接口，而不是它的本身。真正的autos research应该把修改自己，修改本身一个框架作为他的研究内容，仅通过研究自己来提升自己
>
> 或者说，harness也只是一种auto research程序的别名，并不需要一定一定要遵循所谓harness的参考，但是核心在于通过提升自己完成自己，这是auto research的核心。一个可行的方法是harness A来修改harness B，然后harness B来修改harness A 完成自指。现在我的想法是维护一个graph，graph里面有三类节点：
>
> 第一类节点是assumption
>
> 第二类节点是inference产物
>
> 第三类节点是实验的verify
>
> 所谓的inference只由三种reasoning产生，deductive reasoning、abductive reasoning和inductive reasoning

> **3.** 现在的问题在于，当我修改了我这个autoresearch，因为我程序本身之前的research graph可能需要一个快速的验证，哪些东西可以保留，哪些东西不能保留

> **4.** 请challenge和refine我整个的想法 一条一条来跟我讨论

> **5.** 我认为现在需要考虑的事情是，大模型本身具有一定的判断能力，虽然可能不是global optimal的，但是在局部的local的improvement是可以保障，不应该把这个问题等价于传统进化算法中无序的随机的提升

> **6.** 对的 比如你刚才提到的原则 我这里要求auto research的目标是实现auto research 那么如何定义问题 拆解问题 选择原则应该是auto research本身要做的事情

> **7.** 对的 尤其考虑自指这件事情在图中意味着什么

> **8.** 请考虑我曾经指示的 使用A提升B然后使用B 提升A

> **9.** 我认为你现在的理解有一个误区，我这里A和B并不意味着只有两个Agent，A和B是作为auto research的客体而存在的。作为一个research，他自然会imply一些verifiable或者其他的子Agent来完成校验等一系列的工作。这是奥research机制的本身。我这里AB指的是它的auto research整个过程的dynamic，或者说A和B是auto research的快慢版本

> **10.** 给我一个markdown，总结我们的讨论，对于我的输入都需要额外原样保存至appendix

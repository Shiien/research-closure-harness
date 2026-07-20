# Research Closure Harness

一套面向博士研究项目的轻量项目管理系统，目标不是让 AI 替你决定研究方向，而是强制每次工作形成：

\[
\text{Frozen Claim}
\rightarrow
\text{Test}
\rightarrow
\text{Evidence}
\rightarrow
\text{Decision}
\rightarrow
\text{Artifact}
\]

它同时支持：

- **Codex**：通过仓库根目录的 `AGENTS.md`
- **Claude Code**：通过 `CLAUDE.md`、Skill 和可选 hook
- **人类自我管理**：通过每日、实验、每周和 sprint 模板
- **机械检查**：通过无第三方依赖的 Python CLI

---

## 一、核心原则

1. 同时只能有 **一个 active project question**。
2. 同时只能有 **一个 frozen sprint claim**。
3. 同时只能有 **一个主要开放实验**。
4. 开实验前必须写 hypothesis、measurement、kill criterion 和 expected artifact。
5. 做完实验后必须给出 `supported / falsified / inconclusive / terminated` 之一。
6. 没有 decision note，不允许把旧实验当作“完成”。
7. 新想法默认进入 backlog，不立即实现。
8. 每天必须留下可检查 artifact：代码、图、表、证明、文字或明确的负结果。
9. 降低 claim 优先于更换整个研究问题。
10. 毕业阶段优先形成完整初稿，而不是追求统一且完美的理论。

---

## 二、最快开始

将整个目录复制到研究仓库根目录，然后执行：

```bash
python tools/research_closure.py init
python tools/research_closure.py start-sprint \
  --claim "A conditioning quantity predicts affine representation recovery under controlled excitation." \
  --days 14 \
  --artifact "A four-page technical note with one main figure"
python tools/research_closure.py start-day \
  --deliverable "Produce the first conditioning-vs-recovery scatter plot"
python tools/research_closure.py status
```

开实验：

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

关闭实验：

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/conditioning.csv,figures/conditioning_recovery.pdf" \
  --conclusion "Conditioning predicts recovery under controlled excitation."
```

结束一天：

```bash
python tools/research_closure.py close-day \
  --artifact "figures/conditioning_recovery.pdf" \
  --decision "Keep the claim; next isolate estimator variance."
```

检查当前项目是否违反 closure 规则：

```bash
python tools/research_closure.py guard
```

---

## 三、推荐使用方式

### 每天开始

1. 运行 `status`
2. 运行 `start-day`
3. 只填写一个当天 deliverable
4. 让 Codex/Claude Code 先读取当前状态，再开始修改

### 开始任何新实验前

必须创建 experiment card。不要直接让 agent “再试一个变体”。

### 每天结束

必须运行 `close-day`，并指向真实存在的文件。

### 每周一次

使用 `templates/weekly_review.md`。只能选择：

- continue
- narrow claim
- terminate
- advance to next claim

禁止使用“再看看”“再跑一些实验”作为决策。

---

## 四、目录说明

```text
AGENTS.md                         Codex 的持续项目规则
CLAUDE.md                         Claude Code 的持续项目规则
SKILL.md                          技能的可读主副本
.agents/skills/research-closure/  Codex repo skill
.claude/skills/research-closure/  Claude Code repo skill
.research/state.json              CLI 状态
.research/logs/                   每日与实验日志
templates/                        人工填写模板
tools/research_closure.py         无依赖 CLI
.claude/hooks/closure_guard.py    Claude Code 可选机械门禁
docs/protocol.md                  完整研究协议
examples/continual_option_learning/
                                  针对当前研究主线的示例
```

---

## 五、严格程度

默认是 **graduation mode**：

- WIP limit = 1
- sprint = 14 天
- 必须先关闭当前实验，才能创建新的主要实验
- 不允许在没有书面 decision 的情况下修改 sprint claim

如果某些并行实验只是同一实验矩阵的不同 seed，它们属于同一个 experiment card，不应分别创建多个 active experiments。

---

## 六、最重要的使用习惯

这个 harness 不能替你判断一个研究问题是否重要，但它会反复追问：

> 当前工作减少了哪一个明确不确定性？  
> 它将产生什么文件？  
> 什么结果会让我们停止？  
> 完成后我们做出了什么决定？

研究进展不再以“想了多少”计分，而以关闭了多少可检查的不确定性计分。


---

## 全局安装

解压后在目录内运行：

```bash
./install_research_closure_global.sh
```

或者直接让安装器读取 zip：

```bash
./install_research_closure_global.sh \
  --source ./research-closure-harness.zip
```

默认安装：

- Codex 用户 skill：`~/.agents/skills/research-closure/`
- Codex 全局规则：`${CODEX_HOME:-~/.codex}/AGENTS.md`
- Claude Code 用户 skill：`${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/`
- Claude Code 全局规则：`${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`
- CLI：`~/.local/bin/research-closure`
- repo 初始化命令：`~/.local/bin/research-closure-init`

可选安装 Claude Code 的全局 `PreToolUse` hook：

```bash
./install_research_closure_global.sh --with-claude-hook
```

安装器会备份已有的 skill、全局 instruction 文件和 settings。卸载：

```bash
./install_research_closure_global.sh --uninstall
```

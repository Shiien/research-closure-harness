# DeepSeek Harness (dsh) Support

Based on <https://github.com/deepseek-ai/deepseek-harness>.

DeepSeek Harness (`dsh`) is a plugin-based agent harness built on Cordis. It
does **not** use a `DEEPSEEK.md` convention. Its workspace instruction loader
defaults to:

```text
instructionFileCandidates: ["AGENTS.md", "CLAUDE.md"]
```

so entering this repository with `dsh` automatically loads `AGENTS.md`. If
`CLAUDE.md` differs, it is loaded as a second workspace instruction. The A/B
auto-research session rules are therefore kept in `AGENTS.md` as the canonical
auto-loaded file.

## Project skill roots

The dsh filesystem skill provider discovers, in rank order:

| Rank | Source | Path |
|---|---|---|
| 100 | project-dsh | `<projectRoot>/.dsh/skills` |
| 200 | project-agents | `<projectRoot>/.agents/skills` |

This repository ships both roots with the same two A/B skills:

- `.dsh/skills/auto-research-fast/SKILL.md`
- `.dsh/skills/auto-research-slow/SKILL.md`
- `.agents/skills/auto-research-fast/SKILL.md`
- `.agents/skills/auto-research-slow/SKILL.md`

The skill frontmatter is dsh-compatible:

```yaml
name: auto-research-fast
description: ...
whenToUse: ...
disable-model-invocation: false
user-invocable: true
```

## Run

```sh
cd /path/to/this/repo
npx @deepseek-ai/dsh web
```

At session start, dsh renders the workspace instructions from `AGENTS.md`, and
the A/B skills appear in the skill catalog. The model should run:

```sh
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
```

then choose A (`auto-research-fast`) or B (`auto-research-slow`).

## Global installation

`install_research_closure_global.sh` installs dsh skills to:

```text
${DSH_HOME:-~/.dsh}/skills/auto-research-fast/
${DSH_HOME:-~/.dsh}/skills/auto-research-slow/
```

and writes the managed auto-research block into the dsh user-global
instruction file `${DSH_HOME:-~/.dsh}/AGENTS.md`.

## Configuration note

If a deployment wants a separate instruction file, configure
`@deepseek-ai/dsh-agent-instructions` with an explicit
`instructionFileCandidates` list. This repository intentionally keeps the
default convention and does not depend on a custom candidate.

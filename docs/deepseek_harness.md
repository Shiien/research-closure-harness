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
python tools/auto_research.py retro next
```

then choose A (`auto-research-fast`) or B (`auto-research-slow`).

## Stock `minimal` preset cannot auto-load auto-research

The shipped `minimal` preset is a fixed-prompt, two-tool coding-agent
composition. It mounts only persistent bash/pwsh and `str_replace_editor`. It
does **not** mount:

- `@deepseek-ai/dsh-agent-instructions` — so `AGENTS.md`/`CLAUDE.md` are not
  rendered into the session;
- `@deepseek-ai/dsh-skill-filesystem` or `@deepseek-ai/dsh-tool-skill` — so
  `.dsh/skills/` and `.agents/skills/` are neither discovered nor loadable;
- any filesystem tool, subagent, todo, or compaction stack.

A stock `minimal` conversation therefore starts auto-research **only manually**:
the model still has bash and can follow a user instruction such as
"read AGENTS.md and run `python tools/auto_research.py ab-next`". Nothing is
auto-loaded.

## Auto-research preset derived from minimal

This repository ships a user-authored preset template:

```text
dsh/agent-presets/auto-research-minimal/
  agent.cordis.yml
  preset.yml
```

It keeps the same two tools as `minimal` and adds the missing rows:

- `@deepseek-ai/dsh-persona` with `complete: false`;
- `@deepseek-ai/dsh-agent-instructions` with `maxBytes: 65536`;
- `@deepseek-ai/dsh-skill-filesystem`;
- `@deepseek-ai/dsh-tool-skill`.

Install it to the dsh user preset root:

```sh
mkdir -p "${DSH_HOME:-$HOME/.dsh}/.agent-presets"
cp -R dsh/agent-presets/auto-research-minimal \
  "${DSH_HOME:-$HOME/.dsh}/.agent-presets/"
```

Then start a new dsh conversation and select the **Auto Research Minimal**
preset. It behaves like `minimal` but auto-loads `AGENTS.md`, discovers the A/B
skills, and can run the auto-research loop through bash.

## Global installation

`install_research_closure_global.sh` installs dsh skills to:

```text
${DSH_HOME:-~/.dsh}/skills/auto-research-fast/
${DSH_HOME:-~/.dsh}/skills/auto-research-slow/
```

writes the managed auto-research block into the dsh user-global
instruction file `${DSH_HOME:-~/.dsh}/AGENTS.md`, and installs the
`auto-research-minimal` preset under
`${DSH_HOME:-~/.dsh}/.agent-presets/`.

## Configuration note

If a deployment wants a separate instruction file, configure
`@deepseek-ai/dsh-agent-instructions` with an explicit
`instructionFileCandidates` list. This repository intentionally keeps the
default convention and does not depend on a custom candidate.

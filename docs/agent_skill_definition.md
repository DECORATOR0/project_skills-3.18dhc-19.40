# Agent Skill Definition

## Sources
- Anthropic cookbook: `https://platform.claude.com/cookbook/skills-notebooks-01-skills-introduction`
- Anthropic docs: `https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview`
- Agent Skills open spec: `https://agentskills.io/home`
- Agent Skills spec: `https://agentskills.io/specification.md`
- Agent Skills support guide: `https://agentskills.io/client-implementation/adding-skills-support.md`
- Agent Skills best practices: `https://agentskills.io/skill-creation/best-practices.md`
- Agent Skills script guide: `https://agentskills.io/skill-creation/using-scripts.md`

## What A Skill Is
Anthropic defines an Agent Skill as a modular, filesystem-based capability package that extends a general LLM with reusable task knowledge. A skill is not just a prompt. It is a discoverable directory that contains:

1. lightweight metadata used for trigger/discovery
2. a main instruction file loaded only when activated
3. optional scripts, references, and assets loaded on demand

This design enables progressive disclosure:
- Level 1 metadata is always visible to the model
- Level 2 instructions are loaded only after the skill is triggered
- Level 3 resources such as scripts and references are loaded or executed only when needed

## Canonical Directory Structure

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
├── assets/
└── ...
```

`SKILL.md` is mandatory. The other directories are optional.

## Required `SKILL.md` Components

`SKILL.md` must contain YAML frontmatter followed by markdown instructions.

Required frontmatter:
- `name`
- `description`

Optional frontmatter:
- `license`
- `compatibility`
- `metadata`
- `allowed-tools`

Key constraints from the spec:
- `name` must be lowercase, hyphenated, <= 64 chars, and match the parent directory
- `description` must describe both what the skill does and when it should trigger

## Authoring Principles Used In This Project
- Use the gold trajectory of a successful task as the main source of reusable procedure
- Keep `SKILL.md` short and operational
- Move long references and edge-case material into `references/`
- Move fragile logic, arithmetic, parsing, and repeated computation into `scripts/`
- Favor high-trigger descriptions that include both task family and invocation cues
- Design for weaker executor models by providing defaults and reducing ambiguity

## How This Project Uses Skills
- Router sees only the skill catalog headers
- Executor activates one selected skill by loading its `SKILL.md`
- Executor may then read `references/*` or run `scripts/*`
- Critic evaluates whether a new skill, merged skill, or modified skill is needed
- Actor edits the skill library based on critic reward and the experience buffer

## GitHub Tooling References
The actor/executor tool layer in this project is inspired by public tool-serving patterns rather than copied directly:
- `editor-mcp` for safer file editing workflows
- MCP filesystem server implementations for path-scoped file operations
- `agentskills/agentskills` for skill structure and progressive disclosure

In this repository, those ideas are implemented as a lightweight local toolbox:
- workspace-scoped `read_file`, `write_file`, `replace_in_file`, `glob_search`, `list_dir`
- `run_shell` and `run_python_script`
- EO tool bridge that directly imports `agent/tools/*.py`

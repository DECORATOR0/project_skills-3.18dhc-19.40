# AGENTS.md

## Project Collaboration Rules

- Prefer solving local environment and collaboration mismatches with data placement, directory layout, config, launch wrappers, symlinks, and documentation before changing shared Python behavior.
- Treat the existing Python tool and file contract as a shared interface for the whole project. Do not patch it just to fit one machine, one temporary path, or one person's local workspace.
- When data is missing or misplaced, move or mirror the data into the layout expected by the current project code.
- When a compatibility layer is needed for one run, prefer non-invasive adapters such as directory reorganization, symlink bridges, or sidecar scripts.
- If a Python change is still necessary, it should improve the shared contract for collaborators and remain valid after others pull the code.

## Runs

- `runs/` has additional directory-specific rules in [runs/AGENTS.md](runs/AGENTS.md).

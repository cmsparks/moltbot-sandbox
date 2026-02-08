---
name: pokemon-showdown-client
description: Stateless CLI client for Pokemon Showdown websocket battles using the repo's login/message flow. Use when you need to log in with a provided Showdown username/password, start a ladder random battle, poll the current battle state/options, or submit a specific choice without any battle decision logic.
---

# Pokemon Showdown Client

## Overview

Use the bundled CLI to start a ladder battle, poll the latest battle request/options, and submit a choice. The interface is non-interactive and stateless per command, but it persists `battle_id`, `rqid`, and credentials to a state file to minimize errors.

## Quick Start

All commands run from the repo root and require login credentials at least once. By default, state is saved to `ps_client_state.json` in the current working directory.

Start a random ladder battle (returns `battle_id` and first `rqid`):

```bash
python /Users/csparks/.codex/skills/pokemon-showdown-client/scripts/ps_client.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username "YOUR_USERNAME" \
  --ps-password "YOUR_PASSWORD" \
  start \
  --pokemon-format gen9randombattle
```

Poll the battle state and options (returns `request` + `options` JSON and updates state):

```bash
python /Users/csparks/.codex/skills/pokemon-showdown-client/scripts/ps_client.py \
  poll \
  --battle-id "battle-gen9randombattle-XXXX"
```

Submit a choice (non-interactive). It auto-polls for the latest `rqid`, then waits for the next request and returns updated options:

```bash
python /Users/csparks/.codex/skills/pokemon-showdown-client/scripts/ps_client.py \
  choose \
  --battle-id "battle-gen9randombattle-XXXX" \
  --choice "move 1"
```

## Tasks

### 1) Start a Battle

Use the `start` subcommand to queue a ladder battle. This returns `battle_id` and also waits for the first `|request|` so `rqid` is available and saved.

Inputs:
- `--pokemon-format` (use random formats like `gen9randombattle`)
- `--team` is optional (packed team string); use `None` for random battles
- `--timeout-s` optional
- `--request-timeout-s` optional (wait for first `request`)

Note: `start` automatically enables the battle timer with `/timer on`.

### 2) Poll Current Battle State and Options

Use `poll` to join a battle room and return the latest `|request|` JSON plus computed `options`:
- `options.moves` lists legal moves with slot numbers
- `options.switches` lists legal switches with slot numbers
- `options.can_terastallize`, `options.trapped`, `options.force_switch` if present

`poll` also returns `rqid` and updates the state file.

### 3) Submit a Choice

Use `choose` to submit a specific choice string. Examples:
- `move 1`
- `switch 2`
- `move 1 terastallize`

By default, `choose` first polls for the latest `request` to get a fresh `rqid`, then submits `/choose ... <rqid>`, waits for the next `request`, and returns the updated `request` and `options`.

`choose` also returns an `events` array containing the raw battle lines observed between submitting the choice and receiving the next request. Use this to let an LLM reason about what happened in the turn. If the game ends, it returns `finished: true` with `winner` or `tie`.

If you want to skip the pre-poll, pass `--no-refresh` and provide `--rqid` or rely on the saved state.

## State File

The client persists the following to `--state-path` (default `ps_client_state.json`):
- `ps_username`, `ps_password`
- `websocket_uri`
- `battle_id`, `rqid`, `turn`
- last `request`

This enables `poll` and `choose` to work without re-specifying battle ids or credentials every time.

## Script

- `scripts/ps_client.py`: Stateless websocket client derived from the repo's `fp/websocket_client.py` login + message flow. It does not include any battle logic.

## Notes

- Dependencies: `requests` and `websockets` (already in `requirements.txt`).
- This skill is intentionally scoped to client mechanics only; decision-making is handled elsewhere.

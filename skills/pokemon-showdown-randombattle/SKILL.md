---
name: pokemon-showdown-randombattle
description: Run a single automated Pokemon Showdown random battle using this repo's websocket client and run.py. Use when asked to log in with a provided Showdown username/password and start one ladder random battle (e.g. gen9randombattle).
---

# Pokemon Showdown Randombattle

## Goal

Run exactly one ladder random battle using the existing battle loop. This skill is scoped to random formats only.

## Inputs

- `ps_username`
- `ps_password`
- Optional `websocket_uri` (default shown below)
- Optional `pokemon_format` (default `gen9randombattle`)

## Run

Run one ladder match from the repo root:

```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username "YOUR_USERNAME" \
  --ps-password "YOUR_PASSWORD" \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle \
  --run-count 1
```

Wait for the battle to finish. The bot auto-plays the match, sends `gg`, leaves the room, then exits.

## Notes

If dependencies are missing, install them once with `pip install -r requirements.txt` (requires Rust for `poke-engine`).

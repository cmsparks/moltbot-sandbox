#!/usr/bin/env python3
"""
Stateless Pokemon Showdown websocket client.

Subcommands:
  start  - login and start a ladder battle
  poll   - join a battle and return the latest request/options
  choose - submit a choice for a battle
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets


class LoginError(Exception):
    pass


class RequestTimeout(Exception):
    pass


@dataclass
class BattleInit:
    battle_id: str
    title: str | None


class PSWebsocketClient:
    websocket = None
    address = None
    login_uri = None
    username = None
    password = None
    last_message = None

    @classmethod
    async def create(cls, username: str, password: str | None, address: str):
        self = PSWebsocketClient()
        self.username = username
        self.password = password
        self.address = address
        self.websocket = await websockets.connect(self.address)
        self.login_uri = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        return self

    async def receive_message(self) -> str:
        return await self.websocket.recv()

    async def send_message(self, room: str, message_list: list[str]) -> None:
        message = room + "|" + "|".join(message_list)
        await self.websocket.send(message)
        self.last_message = message

    async def close(self) -> None:
        await self.websocket.close()

    async def get_id_and_challstr(self) -> tuple[str, str]:
        while True:
            message = await self.receive_message()
            for room, line in iter_messages(message):
                if line.startswith("|challstr|"):
                    _, _, client_id, challstr = line.split("|", 3)
                    return client_id, challstr

    async def login(self) -> str:
        client_id, challstr = await self.get_id_and_challstr()

        guest_login = self.password is None

        if guest_login:
            response = requests.post(
                self.login_uri,
                data={
                    "act": "getassertion",
                    "userid": self.username,
                    "challstr": "|".join([client_id, challstr]),
                },
            )
        else:
            response = requests.post(
                self.login_uri,
                data={
                    "name": self.username,
                    "pass": self.password,
                    "challstr": "|".join([client_id, challstr]),
                },
            )

        if response.status_code != 200:
            raise LoginError("Could not get assertion")

        if guest_login:
            assertion = response.text
        else:
            response_json = json.loads(response.text[1:])
            if "actionsuccess" not in response_json:
                raise LoginError("Could not log-in: {}".format(response_json))
            assertion = response_json.get("assertion")

        message = ["/trn " + self.username + ",0," + assertion]
        await self.send_message("", message)
        await asyncio.sleep(1)
        return self.username if guest_login else response_json["curuser"]["userid"]

    async def join_room(self, room_name: str) -> None:
        await self.send_message("", ["/join {}".format(room_name)])


def iter_messages(raw: str):
    room = ""
    for line in raw.split("\n"):
        if not line:
            continue
        if line.startswith(">"):
            room = line[1:]
            continue
        yield room, line


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def resolve_config(args: argparse.Namespace) -> tuple[str, str | None, str, Path]:
    state_path = Path(args.state_path).expanduser()
    state = load_state(state_path)

    username = args.ps_username or state.get("ps_username")
    password = args.ps_password if args.ps_password is not None else state.get("ps_password")
    websocket_uri = args.websocket_uri or state.get("websocket_uri")

    if not username:
        raise ValueError("ps_username is required (or provide in state)")
    if not websocket_uri:
        raise ValueError("websocket_uri is required (or provide in state)")

    return username, password, websocket_uri, state_path


async def wait_for_battle_init(client: PSWebsocketClient, timeout_s: int) -> BattleInit:
    deadline = time.time() + timeout_s
    battle_id = None
    title = None
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            message = await asyncio.wait_for(client.receive_message(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        for room, line in iter_messages(message):
            if line == "|init|battle":
                battle_id = room
            elif battle_id and room == battle_id and line.startswith("|title|"):
                title = line.split("|", 2)[2]
                return BattleInit(battle_id=battle_id, title=title)

        if battle_id:
            return BattleInit(battle_id=battle_id, title=title)

    raise RequestTimeout("Timed out waiting for battle to start")


async def wait_for_request(
    client: PSWebsocketClient,
    battle_id: str,
    timeout_s: int,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    deadline = time.time() + timeout_s
    last_request = None
    last_turn = None
    last_error = None

    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            message = await asyncio.wait_for(client.receive_message(), timeout=remaining)
        except asyncio.TimeoutError:
            break

        for room, line in iter_messages(message):
            if room != battle_id:
                continue
            if line.startswith("|error|"):
                last_error = line.split("|", 2)[2]
            if line.startswith("|turn|"):
                try:
                    last_turn = int(line.split("|", 2)[2])
                except ValueError:
                    pass
            if line.startswith("|request|"):
                payload = line.split("|", 2)[2]
                if payload:
                    last_request = json.loads(payload)
                else:
                    last_request = {}
                return last_request, last_turn, last_error

    return last_request, last_turn, last_error


async def wait_for_request_with_events(
    client: PSWebsocketClient,
    battle_id: str,
    timeout_s: int,
) -> tuple[dict[str, Any] | None, int | None, str | None, list[str], bool, str | None, bool]:
    deadline = time.time() + timeout_s
    last_request = None
    last_turn = None
    last_error = None
    events: list[str] = []
    finished = False
    winner = None
    tie = False

    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            message = await asyncio.wait_for(client.receive_message(), timeout=remaining)
        except asyncio.TimeoutError:
            break

        for room, line in iter_messages(message):
            if room != battle_id:
                continue
            if line.startswith("|win|"):
                winner = line.split("|", 2)[2]
                finished = True
                events.append(line)
                return last_request, last_turn, last_error, events, finished, winner, tie
            if line.startswith("|tie|"):
                tie = True
                finished = True
                events.append(line)
                return last_request, last_turn, last_error, events, finished, winner, tie
            if line.startswith("|error|"):
                last_error = line.split("|", 2)[2]
                events.append(line)
                continue
            if line.startswith("|turn|"):
                try:
                    last_turn = int(line.split("|", 2)[2])
                except ValueError:
                    pass
                events.append(line)
                continue
            if line.startswith("|request|"):
                payload = line.split("|", 2)[2]
                if payload:
                    last_request = json.loads(payload)
                else:
                    last_request = {}
                return last_request, last_turn, last_error, events, finished, winner, tie
            events.append(line)

    return last_request, last_turn, last_error, events, finished, winner, tie


def build_options(request_json: dict[str, Any] | None) -> dict[str, Any]:
    options = {
        "moves": [],
        "switches": [],
        "can_terastallize": None,
        "trapped": False,
        "force_switch": False,
        "wait": False,
    }

    if not request_json:
        return options

    if request_json.get("wait") is True:
        options["wait"] = True
        return options

    active = None
    if request_json.get("active"):
        active = request_json["active"][0]
        if "canTerastallize" in active:
            options["can_terastallize"] = active.get("canTerastallize")
        if active.get("trapped"):
            options["trapped"] = True

    if request_json.get("forceSwitch"):
        options["force_switch"] = True

    if active and active.get("moves"):
        for idx, move in enumerate(active["moves"], start=1):
            if move.get("disabled"):
                continue
            options["moves"].append(
                {
                    "slot": idx,
                    "id": move.get("id"),
                    "name": move.get("move"),
                    "pp": move.get("pp"),
                    "maxpp": move.get("maxpp"),
                    "target": move.get("target"),
                }
            )

    side = request_json.get("side")
    if side and side.get("pokemon"):
        for idx, pkmn in enumerate(side["pokemon"], start=1):
            if pkmn.get("active"):
                continue
            condition = pkmn.get("condition", "")
            if "fnt" in condition:
                continue
            options["switches"].append(
                {
                    "slot": idx,
                    "ident": pkmn.get("ident"),
                    "details": pkmn.get("details"),
                    "condition": condition,
                }
            )

    return options


async def start_battle(args: argparse.Namespace) -> None:
    username, password, websocket_uri, state_path = resolve_config(args)
    client = await PSWebsocketClient.create(username, password, websocket_uri)
    try:
        await client.login()
        if args.team is not None:
            await client.send_message("", ["/utm {}".format(args.team)])
        else:
            await client.send_message("", ["/utm None"])
        await client.send_message("", ["/search {}".format(args.pokemon_format)])
        init = await wait_for_battle_init(client, args.timeout_s)
        await client.send_message(init.battle_id, ["/timer on"])

        request_json, turn, error = await wait_for_request(
            client, init.battle_id, args.request_timeout_s
        )
        options = build_options(request_json)
        rqid = request_json.get("rqid") if request_json else None

        state = load_state(state_path)
        state.update(
            {
                "websocket_uri": websocket_uri,
                "ps_username": username,
                "ps_password": password,
                "battle_id": init.battle_id,
                "rqid": rqid,
                "turn": turn,
                "request": request_json,
                "updated_at": time.time(),
            }
        )
        save_state(state_path, state)

        output = {
            "battle_id": init.battle_id,
            "title": init.title,
            "turn": turn,
            "rqid": rqid,
            "error": error,
            "request": request_json,
            "options": options,
            "state_path": str(state_path),
        }
        print(json.dumps(output))
    finally:
        await client.close()


async def poll_battle(args: argparse.Namespace) -> None:
    username, password, websocket_uri, state_path = resolve_config(args)
    battle_id = args.battle_id
    if not battle_id:
        battle_id = load_state(state_path).get("battle_id")
    if not battle_id:
        raise ValueError("battle_id is required (or provide in state)")

    client = await PSWebsocketClient.create(username, password, websocket_uri)
    try:
        await client.login()
        await client.join_room(battle_id)
        request_json, turn, error = await wait_for_request(
            client, battle_id, args.timeout_s
        )
        options = build_options(request_json)
        rqid = request_json.get("rqid") if request_json else None

        state = load_state(state_path)
        state.update(
            {
                "websocket_uri": websocket_uri,
                "ps_username": username,
                "ps_password": password,
                "battle_id": battle_id,
                "rqid": rqid,
                "turn": turn,
                "request": request_json,
                "updated_at": time.time(),
            }
        )
        save_state(state_path, state)

        output = {
            "battle_id": battle_id,
            "turn": turn,
            "error": error,
            "rqid": rqid,
            "request": request_json,
            "options": options,
            "state_path": str(state_path),
        }
        print(json.dumps(output))
    finally:
        await client.close()


async def choose_action(args: argparse.Namespace) -> None:
    username, password, websocket_uri, state_path = resolve_config(args)
    battle_id = args.battle_id
    if not battle_id:
        battle_id = load_state(state_path).get("battle_id")
    if not battle_id:
        raise ValueError("battle_id is required (or provide in state)")

    client = await PSWebsocketClient.create(username, password, websocket_uri)
    try:
        await client.login()
        await client.join_room(battle_id)
        choice = args.choice.strip()
        if not choice:
            raise ValueError("choice must be non-empty")
        if choice.startswith("/choose "):
            payload = choice
        else:
            payload = "/choose " + choice

        rqid = args.rqid
        request_json = None
        turn = None
        error = None
        if not args.no_refresh:
            request_json, turn, error = await wait_for_request(
                client, battle_id, args.timeout_s
            )
            if request_json:
                rqid = request_json.get("rqid")
        elif rqid is None:
            rqid = load_state(state_path).get("rqid")

        if rqid is None:
            raise ValueError("rqid is required (or use refresh polling)")

        await client.send_message(battle_id, [payload, str(rqid)])

        # wait for the next request (next turn/state) after submitting the choice
        next_request, next_turn, next_error, events, finished, winner, tie = await wait_for_request_with_events(
            client, battle_id, args.post_timeout_s
        )
        next_options = build_options(next_request)
        next_rqid = next_request.get("rqid") if next_request else None

        state = load_state(state_path)
        state.update(
            {
                "websocket_uri": websocket_uri,
                "ps_username": username,
                "ps_password": password,
                "battle_id": battle_id,
                "rqid": next_rqid or rqid,
                "turn": next_turn or turn,
                "request": next_request or request_json,
                "finished": finished,
                "winner": winner,
                "tie": tie,
                "updated_at": time.time(),
            }
        )
        save_state(state_path, state)

        output = {
            "battle_id": battle_id,
            "sent": payload,
            "rqid": rqid,
            "error": next_error or error,
            "turn": next_turn,
            "request": next_request,
            "options": next_options,
            "events": events,
            "finished": finished,
            "winner": winner,
            "tie": tie,
            "state_path": str(state_path),
        }
        print(json.dumps(output))
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pokemon Showdown stateless client")
    parser.add_argument(
        "--websocket-uri",
        required=False,
        help="e.g. wss://sim3.psim.us/showdown/websocket",
    )
    parser.add_argument("--ps-username", required=False)
    parser.add_argument("--ps-password", default=None)
    parser.add_argument(
        "--state-path",
        default="ps_client_state.json",
        help="Path to persist battle_id/rqid/credentials",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start a ladder battle")
    start.add_argument("--pokemon-format", required=True)
    start.add_argument("--team", default=None, help="Packed team or 'None'")
    start.add_argument("--timeout-s", type=int, default=60)
    start.add_argument(
        "--request-timeout-s",
        type=int,
        default=30,
        help="How long to wait for the first request after battle start",
    )
    start.set_defaults(func=start_battle)

    poll = subparsers.add_parser("poll", help="Poll current battle request")
    poll.add_argument("--battle-id", required=False)
    poll.add_argument("--timeout-s", type=int, default=30)
    poll.set_defaults(func=poll_battle)

    choose = subparsers.add_parser("choose", help="Submit a battle choice")
    choose.add_argument("--battle-id", required=False)
    choose.add_argument(
        "--choice",
        required=True,
        help="e.g. 'move 1', 'switch 2', 'move 1 terastallize'",
    )
    choose.add_argument(
        "--rqid",
        type=int,
        default=None,
        help="Optional request id (rqid). If omitted, the client polls for it.",
    )
    choose.add_argument("--timeout-s", type=int, default=15)
    choose.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip polling for the latest request before sending /choose",
    )
    choose.add_argument(
        "--post-timeout-s",
        type=int,
        default=30,
        help="How long to wait for the next request after submitting a choice.",
    )
    choose.set_defaults(func=choose_action)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        asyncio.run(args.func(args))
    except (LoginError, RequestTimeout, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()

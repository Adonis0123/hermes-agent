"""``hermes feishu send ...`` (registered via ``ctx.register_cli_command()``): owner overlay.

Always a classic post, never a CardKit stream card, so ``message_id`` is the real ``om_`` id a
script can pass back as ``feishu:<chat_id>:<om_id>`` to reply in its topic. Lives in the plugin
so ``hermes send`` / ``send_message`` stay byte-identical to upstream.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_USAGE_EXIT = 2


def register_cli(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="feishu_command", required=True)
    p_send = subs.add_parser("send", help="Send a classic (non-card) message; the result id is a threadable om_ id")
    p_send.add_argument("-t", "--to", metavar="TARGET", default="feishu", help=(
        "'feishu' (home channel), 'feishu:chat_id', or 'feishu:chat_id:om_thread_id'."))
    p_send.add_argument("message", nargs="?", default=None, help="Message text. If omitted, read from --file or stdin.")
    p_send.add_argument("-f", "--file", metavar="PATH", default=None, help="Read the body from PATH ('-' for stdin).")
    p_send.add_argument("--json", action="store_true", default=False, help="Emit the raw JSON result.")
    p_send.add_argument("-q", "--quiet", action="store_true", default=False, help="No stdout on success.")
    parser.set_defaults(func=dispatch)


def dispatch(args: argparse.Namespace) -> int:
    if getattr(args, "feishu_command", None) != "send":
        print(f"unknown subcommand: {args.feishu_command}", file=sys.stderr)
        return _USAGE_EXIT
    return _cmd_send(args)


def _read_body(args: argparse.Namespace) -> str | None:
    if args.message:
        return args.message
    if args.file == "-" or (args.file is None and not sys.stdin.isatty()):
        return sys.stdin.read() or None
    return Path(args.file).read_text(encoding="utf-8") if args.file else None


def _resolve(target: str) -> tuple[object, str, str | None]:
    """``(pconfig, chat_id, thread_id)``; raises ValueError with a user-facing reason."""
    platform_name, _, ref = target.strip().partition(":")
    if platform_name.strip().lower() != "feishu":
        raise ValueError(f"target must start with 'feishu', got {target!r}")
    from gateway.config import Platform, load_gateway_config
    config = load_gateway_config()
    pconfig = config.platforms.get(Platform("feishu"))
    if not pconfig or not pconfig.enabled:
        raise ValueError("Feishu is not configured (set FEISHU_APP_ID / FEISHU_APP_SECRET)")
    if not ref.strip():
        home = config.get_home_channel(Platform("feishu"))
        if not home or not home.chat_id:
            raise ValueError("no chat_id given and FEISHU_HOME_CHANNEL is not set")
        return pconfig, home.chat_id, None
    from tools.send_message_targets import resolve_send_target
    chat_id, thread_id, err = resolve_send_target("feishu", ref.strip(), pass_unresolved_references=True)
    if err or not chat_id:
        raise ValueError(err or f"cannot resolve target {target!r}")
    return pconfig, chat_id, thread_id


def _cmd_send(args: argparse.Namespace) -> int:
    from hermes_cli.send_cmd import _load_hermes_env
    _load_hermes_env()  # the gateway config loader reads credentials from os.environ
    try:
        message = _read_body(args)
    except OSError as exc:
        print(f"hermes feishu send: cannot read {args.file}: {exc}", file=sys.stderr)
        return _USAGE_EXIT
    if not message or not message.strip():
        print("hermes feishu send: no message provided (positional, --file, or stdin)", file=sys.stderr)
        return _USAGE_EXIT
    try:
        pconfig, chat_id, thread_id = _resolve(args.to)
    except ValueError as exc:
        result = {"error": str(exc)}
    else:
        from .adapter import _standalone_send
        result = asyncio.run(_standalone_send(pconfig, chat_id, message, thread_id=thread_id, plain=True))
    ok = isinstance(result, dict) and bool(result.get("success"))
    if ok:  # same best-effort mirror `hermes send` does, so a topic reply sees what was relayed
        try:
            from gateway.mirror import mirror_to_session
            result["mirrored"] = bool(mirror_to_session("feishu", chat_id, message.strip(), thread_id=thread_id))
        except Exception:
            result["mirrored"] = False
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif not ok:
        print(f"hermes feishu send: {result.get('error') if isinstance(result, dict) else result}", file=sys.stderr)
    elif not args.quiet:
        print(result.get("message_id") or "sent")
    return 0 if ok else 1

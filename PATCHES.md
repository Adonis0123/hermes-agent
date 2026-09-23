English | [简体中文](PATCHES.zh-CN.md)

# Fork patches

This fork is [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) plus a short, linear overlay.
`main` = upstream + the commits below, re-applied by **rebase** on every upstream sync.
`upstream-main` is the upstream commit the current overlay is based on; it moves forward at each sync. Each sync is tagged `v<upstream-tag>-adonis.<n>`.

| # | Patch | Area | Upstream-able? |
|---|-------|------|----------------|
| 1 | [Keep one edit stream across tool rounds](#1-keep-one-edit-stream-across-tool-rounds) | gateway core | Maybe |
| 2 | [Quiet Feishu replies, CardKit stream card, plugin send](#2-quiet-feishu-replies-cardkit-stream-card-plugin-send) | Feishu plugin | Partly |
| 3 | [Owner tips and a short model line on `/new`](#3-owner-tips-and-a-short-model-line-on-new) | gateway + CLI config | Partly |

## 1. Keep one edit stream across tool rounds

Commit: [`e9b8eb2635`](https://github.com/Adonis0123/hermes-agent/commit/e9b8eb2635ede18f0996fabd34114d687f9e8157)

**What it does.** The stream consumer asks the adapter an optional question,
`stream_is_message_for_chat(chat_id, metadata)`. When the adapter answers `True`, a tool
segment break no longer finalizes the edit-transport message, and the whole turn is one
cumulative stream. Adapters without the method behave exactly as upstream.
The Feishu CardKit card (patch 2) uses it so one answer stays in one card across tool rounds.

**Files.** `gateway/stream_consumer.py`

**Config keys.** None. The hook is dormant unless an adapter implements it.

**Upstream-able?** Maybe. It is a small, opt-in adapter capability. Upstream is moving
stream-as-message delivery to its draft transport, so it would need to be proposed as an
adapter capability rather than a Feishu special case.

## 2. Quiet Feishu replies, CardKit stream card, plugin send

Commit: [`feff7a85f5`](https://github.com/Adonis0123/hermes-agent/commit/feff7a85f5a0b929bc1a4795be85437cda4b2ccf)

**What it does.**

- `@_all` (@everyone) is not treated as a mention of the bot.
- Inside a topic the bot already joined, a follow-up reply continues the session without a new @mention.
- In group chats the first @mention opens a message topic on that message, and replies go there. DMs stay flat.
- Reactions on bot messages are ignored by default. When routing is on, plain acknowledgement emojis (thumbs-up, OK, ...) are still dropped.
- Optional CardKit stream card: one card per turn, kept open across tool rounds (patch 1), reopened
  when CardKit already closed streaming mode, and replaced by a classic message when the card cannot
  be created, so the start of the answer is never lost.
- `hermes feishu send`: a plugin CLI command that always posts a classic message. The returned id is
  a threadable `om_` id, and the text is mirrored into the target session like `hermes send`.
- Feishu keys in `config.yaml` may sit under `platforms.feishu` or `platforms.feishu.extra`.

**Files.** `plugins/platforms/feishu/adapter.py`, `plugins/platforms/feishu/cli.py`,
`tests/gateway/test_feishu_overlay_contracts.py`, `tests/gateway/test_feishu_stream_card.py`

**Config keys** (`platforms.feishu.extra.*`, env var in brackets; per-chat override via `group_rules.<chat_id>` where noted):

| Key | Default | Meaning |
|-----|---------|---------|
| `thread_followup_without_mention` (`FEISHU_THREAD_FOLLOWUP_WITHOUT_MENTION`) | `true` | Topic follow-ups need no @mention |
| `auto_thread` (`FEISHU_AUTO_THREAD`) | `true` | First group @mention opens a topic |
| `route_inbound_reactions` (`FEISHU_ROUTE_INBOUND_REACTIONS`) | `false` | Route reactions on bot messages to the agent |
| `stream_card` (`FEISHU_STREAM_CARD`), per-chat | `false` | Stream answers into a CardKit card |
| `stream_card_in_thread` (`FEISHU_STREAM_CARD_IN_THREAD`), per-chat | `false` | Also use the card inside topics |
| `stream_card_title` (`FEISHU_STREAM_CARD_TITLE`), per-chat | `Hermes` | Header title of the stream card |

Card streaming follows the global `streaming.enabled` switch.

**Upstream-able?** Partly.
The quiet-group defaults (`@_all`, topic follow-up, auto-topic) are owner preferences and
deliberately contradict three upstream tests in `tests/gateway/test_feishu.py`
(`test_at_all_still_requires_policy_gate`, `test_explicit_thread_id_is_preserved`,
`test_regular_reply_root_id_does_not_become_thread_id`); these fail on this fork by design.
The CardKit card and `hermes feishu send` are generic and could be offered as separate opt-in PRs.

## 3. Owner tips and a short model line on `/new`

Commit: [`ffbf2d0512`](https://github.com/Adonis0123/hermes-agent/commit/ffbf2d05127c3c8bc2bcd60e296f85dc99a9c3a1)

**What it does.** `display.tips` supplies the line appended to gateway `/new` and `/reset`.
A non-empty list is the whole pool: one line is drawn at random, printed without the tip label,
and the same chat does not get the same line twice in a row. The reset notice shows only the
Model line; provider and context length stay on `/status`.

**Files.** `gateway/run_turn.py`, `gateway/slash_commands_session.py`, `hermes_cli/config_defaults.py`,
`hermes_cli/tips.py`, `website/docs/user-guide/configuration.md`,
`tests/gateway/test_reset_display_tips.py`, `tests/gateway/test_session_info.py`, `tests/hermes_cli/test_display_tips.py`

**Config keys.** `display.tips` (list of strings, default `[]` = built-in tips).

**Upstream-able?** Partly. `display.tips` is a generic setting. The shorter reset notice is a
preference and removes information upstream shows on purpose.

## Sync procedure

Run in the fork checkout, with `origin` = this repo and `upstream` = NousResearch
(adjust remote names to your clone).

```bash
git fetch upstream main
git switch main
old_base=$(git merge-base main upstream/main)   # upstream commit the overlay sits on now
git branch -f pre-sync main                   # local safety anchor
git branch -f upstream-main upstream/main
git rebase upstream-main                      # re-apply the overlay
git range-diff "$old_base"..pre-sync upstream-main..main   # review each patch after the rebase
scripts/run_tests.sh tests/gateway/test_feishu.py -q   # 3 known failures listed in patch 2
scripts/run_tests.sh tests/gateway/test_feishu_overlay_contracts.py \
  tests/gateway/test_feishu_stream_card.py tests/gateway/test_reset_display_tips.py \
  tests/gateway/test_session_info.py tests/hermes_cli/test_display_tips.py -q
BASE=$(git describe --tags --abbrev=0 upstream-main)   # e.g. v2026.9.21
TAG="$BASE-adonis.<n>"; echo "$TAG"   # n = 1, 2, ... per sync on the same upstream tag
git tag "$TAG"
git push origin upstream-main
git push --force-with-lease origin main
git push origin "$TAG"
```

If a patch conflicts, re-read the new upstream code first and keep the patch's intent;
update the patch's section here when its files or keys change.

## Getting updates as a user of this fork

Clone this fork so that `origin` points to it. `hermes update` fast-forwards to `origin/main` and
resets to it when history diverged (which happens after each rebase-and-force-push sync).
Because `main` carries commits that upstream does not have, the updater's upstream check
(once you let it add an `upstream` remote) prints
"Skipping upstream sync to preserve your changes" and does not pull NousResearch directly;
you receive upstream changes when this fork syncs.
Uncommitted local changes are stashed around the update. Keep your own commits on a separate
branch: on a custom branch the updater merges `origin/main` instead of resetting.

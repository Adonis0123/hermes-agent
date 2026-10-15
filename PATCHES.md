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
| 4 | [Retry device-code polls on transport errors](#4-retry-device-code-polls-on-transport-errors) | CLI auth | Yes |
| 5 | [Failed-turn notices follow `display.language`](#5-failed-turn-notices-follow-displaylanguage) | agent + gateway copy | Yes |

## 1. Keep one edit stream across tool rounds

Commits: [`7bbf757f09`](https://github.com/Adonis0123/hermes-agent/commit/7bbf757f090b017b54c790ce15402dd3ef8698d0)

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

Commits: [`8d72901724`](https://github.com/Adonis0123/hermes-agent/commit/8d7290172494c4b01c5ad064498ec873dc67eb0d), [`e5d2891fc4`](https://github.com/Adonis0123/hermes-agent/commit/e5d2891fc48ff7a9ca668916b20eac1afa1c90bf), [`c3316c1889`](https://github.com/Adonis0123/hermes-agent/commit/c3316c1889911034fd3dbd3956e984d2fd29bbba), [`641e165291`](https://github.com/Adonis0123/hermes-agent/commit/641e1652910c1a1c8d2d177ea69558c9d22d03dd), [`12d21e2bc8`](https://github.com/Adonis0123/hermes-agent/commit/12d21e2bc82fa749d1cde26759b65066d91fa5b8)

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
- Markdown tables in the final stream card are rendered as native CardKit tables.
- A topic follow-up that @-mentions someone other than the bot is not admitted, so the agent does not act on a message meant for a teammate. Sender names come from the chat members API.
- A plain reply inside a topic carries `parent_id == root_id`. The quoted text the agent sees is labelled as the topic root, not as a quote, so the agent answers the latest question in the topic.

**Files.** `plugins/platforms/feishu/adapter.py`, `plugins/platforms/feishu/cli.py`,
`tests/gateway/test_feishu_overlay_contracts.py`, `tests/gateway/test_feishu_stream_card.py`,
`tests/gateway/test_feishu_native_tables.py`

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
`test_regular_reply_root_id_does_not_become_thread_id`). They are skipped on this fork; each skip
names the overlay contract that covers the same input.
The CardKit card and `hermes feishu send` are generic and could be offered as separate opt-in PRs.

## 3. Owner tips and a short model line on `/new`

Commits: [`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`42019ab4f6`](https://github.com/Adonis0123/hermes-agent/commit/42019ab4f60b7e6e626649fef59e69c6e209761e), [`973abc6380`](https://github.com/Adonis0123/hermes-agent/commit/973abc6380d7d8b0547f8766b7e2fbccf76a974f)

**What it does.** `display.tips` supplies the line appended to gateway `/new` and `/reset`.
A non-empty list is the whole pool: one line is drawn at random, printed without the tip label,
and the same chat does not get the same line twice in a row. The reset notice shows only the
Model line; provider and context length stay on `/status`.

**Files.** `gateway/run_turn.py`, `gateway/slash_commands_session.py`,
`hermes_cli/tips.py`,
`tests/gateway/test_reset_display_tips.py`, `tests/gateway/test_session_info.py`, `tests/hermes_cli/test_display_tips.py`

**Config keys.** `display.tips` (list of strings, default `[]` = built-in tips).

**Upstream-able?** Partly. `display.tips` is a generic setting. The shorter reset notice is a
preference and removes information upstream shows on purpose.

## 4. Retry device-code polls on transport errors

Commits: [`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`5b5ab87295`](https://github.com/Adonis0123/hermes-agent/commit/5b5ab87295138a6bbd2af8a072a6c8897b2f3e7c)

**What it does.** The shared RFC 8628 poller (`_poll_device_token_generic`, used by the xAI Grok
OAuth and Nous logins) treats an `httpx.TransportError` like `authorization_pending`: it sleeps one
interval and polls again until the code expires. Before, one dropped TLS handshake
(`SSL: UNEXPECTED_EOF_WHILE_READING`, seen against `auth.x.ai`) ended the login while the user was
still approving the code.

**Files.** `hermes_cli/auth_device_flow.py`, `tests/hermes_cli/test_auth_nous_provider.py`

**Config keys.** None.

**Upstream-able?** Yes. It is a small, provider-neutral robustness fix.

## 5. Failed-turn notices follow `display.language`

Commits: [`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`5b5ab87295`](https://github.com/Adonis0123/hermes-agent/commit/5b5ab87295138a6bbd2af8a072a6c8897b2f3e7c)

**What it does.** The assistant boundary row that closes a failed turn ("Your request was not
processed…" / "This turn did not complete…") is chosen in the active language through
`agent.i18n.get_language()` (`HERMES_LANGUAGE` > `display.language` > `en`). Chinese (`zh`) has its
own copy; every other language keeps the English text. `untyped_failed_turn_display_kind` recognizes
the notice in any shipped language, so legacy rows still read back as boundaries. The English
constants and `failed_turn_notice(turn_messages)` stay compatible; it takes an optional `lang=`.

**Files.** `agent/turn_failure_copy.py`, `gateway/run_turn_persistence.py` (moved out of `gateway/run_turn.py` in v0.22.0),
`tests/agent/test_turn_failure_copy_language.py`

**Config keys.** None new; reads `display.language`.

**Upstream-able?** Yes. It is a small i18n extension; upstream would likely want the copy moved into
`locales/*.yaml`.

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
git log --oneline "$old_base"..pre-sync   # every overlay commit must show up in the range-diff
scripts/run_tests.sh tests/gateway/test_feishu.py -q   # the 3 cases in patch 2 are skipped, not failing
scripts/run_tests.sh tests/gateway/test_feishu_overlay_contracts.py \
  tests/gateway/test_feishu_stream_card.py tests/gateway/test_reset_display_tips.py \
  tests/gateway/test_session_info.py tests/hermes_cli/test_display_tips.py \
  tests/gateway/test_feishu_native_tables.py tests/agent/test_turn_failure_copy_language.py \
  tests/hermes_cli/test_auth_nous_provider.py -q
hermes feishu send --help >/dev/null   # plugin CLI that scripts post through
BASE=$(git describe --tags --abbrev=0 upstream-main)   # e.g. v2026.9.21
TAG="$BASE-adonis.<n>"; echo "$TAG"   # n = 1, 2, ... per sync on the same upstream tag
git tag "$TAG"
git push origin upstream-main
git push --force-with-lease origin main
git push origin "$TAG"
```

Code you cannot find in the new upstream may be overlay that was not replayed: check the
`git log` list above before calling it removed upstream.
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

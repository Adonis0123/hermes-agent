[English](PATCHES.md) | 简体中文

# Fork 补丁说明

本 fork 等于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 加一小段线性 overlay（叠加补丁）。
`main` = 上游 + 下列提交，每次同步上游时用 **rebase** 重新叠上。
`upstream-main` 指向当前 overlay 所基于的上游提交，每次同步时向前移动。每次同步打一个 `v<上游 tag>-adonis.<n>` 标签。
本文以英文版为准。

| # | 补丁 | 范围 | 能否提交上游 |
|---|------|------|--------------|
| 1 | [跨工具轮保持同一条编辑流](#1-跨工具轮保持同一条编辑流) | gateway 核心 | 也许 |
| 2 | [飞书安静回复、CardKit 流式卡片、插件发送命令](#2-飞书安静回复cardkit-流式卡片插件发送命令) | 飞书插件 | 部分可以 |
| 3 | [`/new` 自定义提示语和精简模型行](#3-new-自定义提示语和精简模型行) | gateway + CLI 配置 | 部分可以 |

## 1. 跨工具轮保持同一条编辑流

提交：[`e9b8eb2635`](https://github.com/Adonis0123/hermes-agent/commit/e9b8eb2635ede18f0996fabd34114d687f9e8157)

**作用。** stream consumer（流式输出消费者）会调用适配器的可选方法
`stream_is_message_for_chat(chat_id, metadata)`。返回 `True` 时，工具分段不再封住当前编辑消息，
整轮回答算一条累积流。没有这个方法的适配器行为与上游完全一致。
补丁 2 的飞书 CardKit 卡片靠它让一次回答跨工具轮留在同一张卡片里。

**文件。** `gateway/stream_consumer.py`

**配置项。** 无。适配器不实现该方法时这段代码不生效。

**能否提交上游？** 也许。改动小，由适配器自愿开启。上游正把"流即消息"迁到 draft transport，
所以只能以适配器能力的形式提议，不能做成飞书特例。

## 2. 飞书安静回复、CardKit 流式卡片、插件发送命令

提交：[`feff7a85f5`](https://github.com/Adonis0123/hermes-agent/commit/feff7a85f5a0b929bc1a4795be85437cda4b2ccf)

**作用。**

- `@_all`（@所有人）不算 @ 机器人。
- 机器人已加入的话题里，后续回复不用重新 @ 也能接着对话。
- 群聊里第一次 @ 会在该消息上开话题，回复发进话题。私聊保持平铺。
- 默认忽略别人对机器人消息的表情回应。打开转发后，点赞、OK 这类确认表情仍会丢弃。
- 可选 CardKit 流式卡片：每轮一张卡片，跨工具轮保持打开（补丁 1）；CardKit 已关闭流式模式时重新打开；
  卡片建不出来时改发普通消息，回答开头不会丢。
- `hermes feishu send`：插件 CLI 命令，总是发普通消息。返回的 id 是可开话题的 `om_` id，
  文本会像 `hermes send` 一样同步进目标会话。
- `config.yaml` 里的飞书配置项可以放在 `platforms.feishu` 或 `platforms.feishu.extra` 下。

**文件。** `plugins/platforms/feishu/adapter.py`、`plugins/platforms/feishu/cli.py`、
`tests/gateway/test_feishu_overlay_contracts.py`、`tests/gateway/test_feishu_stream_card.py`

**配置项**（`platforms.feishu.extra.*`，括号内是环境变量；标"按群"的可在 `group_rules.<chat_id>` 单独覆盖）：

| 配置项 | 默认值 | 含义 |
|--------|--------|------|
| `thread_followup_without_mention`（`FEISHU_THREAD_FOLLOWUP_WITHOUT_MENTION`） | `true` | 话题内后续回复无需 @ |
| `auto_thread`（`FEISHU_AUTO_THREAD`） | `true` | 群里第一次 @ 开话题 |
| `route_inbound_reactions`（`FEISHU_ROUTE_INBOUND_REACTIONS`） | `false` | 把对机器人消息的表情回应转给 agent |
| `stream_card`（`FEISHU_STREAM_CARD`），按群 | `false` | 回答流式写进 CardKit 卡片 |
| `stream_card_in_thread`（`FEISHU_STREAM_CARD_IN_THREAD`），按群 | `false` | 话题内也用卡片 |
| `stream_card_title`（`FEISHU_STREAM_CARD_TITLE`），按群 | `Hermes` | 流式卡片的标题 |

卡片流式跟随全局 `streaming.enabled` 开关。

**能否提交上游？** 部分可以。
群聊安静默认值（`@_all`、话题续聊、自动开话题）是个人偏好，与 `tests/gateway/test_feishu.py` 里三条上游测试
（`test_at_all_still_requires_policy_gate`、`test_explicit_thread_id_is_preserved`、
`test_regular_reply_root_id_does_not_become_thread_id`）故意相反，这三条在本 fork 上预期失败。
CardKit 卡片和 `hermes feishu send` 是通用能力，可以拆成独立的可选 PR。

## 3. `/new` 自定义提示语和精简模型行

提交：[`ffbf2d0512`](https://github.com/Adonis0123/hermes-agent/commit/ffbf2d05127c3c8bc2bcd60e296f85dc99a9c3a1)

**作用。** `display.tips` 提供 gateway `/new` 和 `/reset` 末尾的那行提示。
列表非空时只从列表里随机抽一行，不带提示标签，同一个聊天不会连续抽到同一行。
重置通知只保留 Model 一行，provider 和上下文长度留给 `/status`。

**文件。** `gateway/run_turn.py`、`gateway/slash_commands_session.py`、`hermes_cli/config_defaults.py`、
`hermes_cli/tips.py`、`website/docs/user-guide/configuration.md`、
`tests/gateway/test_reset_display_tips.py`、`tests/gateway/test_session_info.py`、`tests/hermes_cli/test_display_tips.py`

**配置项。** `display.tips`（字符串列表，默认 `[]` = 内置提示）。

**能否提交上游？** 部分可以。`display.tips` 是通用配置。精简重置通知是个人偏好，去掉了上游有意展示的信息。

## 同步流程

在 fork 的本地仓库里执行，`origin` = 本仓库，`upstream` = NousResearch（按你的 clone 调整 remote 名）。

```bash
git fetch upstream main
git switch main
old_base=$(git merge-base main upstream/main)   # overlay 当前所在的上游提交
git branch -f pre-sync main                   # 本地保险点
git branch -f upstream-main upstream/main
git rebase upstream-main                      # 重新叠加 overlay
git range-diff "$old_base"..pre-sync upstream-main..main   # 逐个核对 rebase 后的补丁
scripts/run_tests.sh tests/gateway/test_feishu.py -q   # 补丁 2 列出的 3 条为已知失败
scripts/run_tests.sh tests/gateway/test_feishu_overlay_contracts.py \
  tests/gateway/test_feishu_stream_card.py tests/gateway/test_reset_display_tips.py \
  tests/gateway/test_session_info.py tests/hermes_cli/test_display_tips.py -q
BASE=$(git describe --tags --abbrev=0 upstream-main)   # 例如 v2026.9.21
TAG="$BASE-adonis.<n>"; echo "$TAG"   # 同一上游 tag 下每次同步 n = 1, 2, ...
git tag "$TAG"
git push origin upstream-main
git push --force-with-lease origin main
git push origin "$TAG"
```

补丁冲突时，先重读上游新代码，保留补丁原意；补丁的文件或配置项变了，同步更新本文对应小节。

## 使用本 fork 时如何获取更新

clone 本 fork，让 `origin` 指向它。`hermes update` 快进到 `origin/main`；历史分叉时（每次 rebase 加强推同步后都会分叉）
直接重置到 `origin/main`。
因为 `main` 带着上游没有的提交，更新程序的上游检查（在你允许它添加 `upstream` remote 之后）会输出
"Skipping upstream sync to preserve your changes"，不会直接拉 NousResearch；上游改动随本 fork 的同步到达。
未提交的本地改动会在更新前后自动 stash。自己的提交请放在单独分支：在自定义分支上，更新程序会合并 `origin/main` 而不是重置。

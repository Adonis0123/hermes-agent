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
| 4 | [设备码轮询遇到网络错误时重试](#4-设备码轮询遇到网络错误时重试) | CLI 认证 | 可以 |
| 5 | [失败轮提示跟随 `display.language`](#5-失败轮提示跟随-displaylanguage) | agent + gateway 文案 | 可以 |

## 1. 跨工具轮保持同一条编辑流

提交：[`7bbf757f09`](https://github.com/Adonis0123/hermes-agent/commit/7bbf757f090b017b54c790ce15402dd3ef8698d0)

**作用。** stream consumer（流式输出消费者）会调用适配器的可选方法
`stream_is_message_for_chat(chat_id, metadata)`。返回 `True` 时，工具分段不再封住当前编辑消息，
整轮回答算一条累积流。没有这个方法的适配器行为与上游完全一致。
补丁 2 的飞书 CardKit 卡片靠它让一次回答跨工具轮留在同一张卡片里。

**文件。** `gateway/stream_consumer.py`

**配置项。** 无。适配器不实现该方法时这段代码不生效。

**能否提交上游？** 也许。改动小，由适配器自愿开启。上游正把"流即消息"迁到 draft transport，
所以只能以适配器能力的形式提议，不能做成飞书特例。

## 2. 飞书安静回复、CardKit 流式卡片、插件发送命令

提交：[`8d72901724`](https://github.com/Adonis0123/hermes-agent/commit/8d7290172494c4b01c5ad064498ec873dc67eb0d), [`e5d2891fc4`](https://github.com/Adonis0123/hermes-agent/commit/e5d2891fc48ff7a9ca668916b20eac1afa1c90bf), [`c3316c1889`](https://github.com/Adonis0123/hermes-agent/commit/c3316c1889911034fd3dbd3956e984d2fd29bbba), [`641e165291`](https://github.com/Adonis0123/hermes-agent/commit/641e1652910c1a1c8d2d177ea69558c9d22d03dd), [`12d21e2bc8`](https://github.com/Adonis0123/hermes-agent/commit/12d21e2bc82fa749d1cde26759b65066d91fa5b8)

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
- 流式卡片收尾时，markdown 表格渲染成 CardKit 原生表格。
- 话题里的追问如果 @ 的是别人而不是机器人，不会被接收，agent 不会去执行发给同事的话。发送人名字从群成员接口取。
- 话题里不点引用直接回复时，飞书给的 `parent_id` 等于 `root_id`。agent 看到的引用内容会标成「话题首条」而不是引用，agent 按话题里最新的提问作答。

**文件。** `plugins/platforms/feishu/adapter.py`、`plugins/platforms/feishu/cli.py`、
`tests/gateway/test_feishu_overlay_contracts.py`、`tests/gateway/test_feishu_stream_card.py`、
`tests/gateway/test_feishu_native_tables.py`

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
`test_regular_reply_root_id_does_not_become_thread_id`）故意相反。这三条在本 fork 上标为 skip，
每条都写明由哪条叠层契约测试覆盖同样的输入。
CardKit 卡片和 `hermes feishu send` 是通用能力，可以拆成独立的可选 PR。

## 3. `/new` 自定义提示语和精简模型行

提交：[`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`42019ab4f6`](https://github.com/Adonis0123/hermes-agent/commit/42019ab4f60b7e6e626649fef59e69c6e209761e), [`973abc6380`](https://github.com/Adonis0123/hermes-agent/commit/973abc6380d7d8b0547f8766b7e2fbccf76a974f)

**作用。** `display.tips` 提供 gateway `/new` 和 `/reset` 末尾的那行提示。
列表非空时只从列表里随机抽一行，不带提示标签，同一个聊天不会连续抽到同一行。
重置通知只保留 Model 一行，provider 和上下文长度留给 `/status`。

**文件。** `gateway/run_turn.py`、`gateway/slash_commands_session.py`、
`hermes_cli/tips.py`、
`tests/gateway/test_reset_display_tips.py`、`tests/gateway/test_session_info.py`、`tests/hermes_cli/test_display_tips.py`

**配置项。** `display.tips`（字符串列表，默认 `[]` = 内置提示）。

**能否提交上游？** 部分可以。`display.tips` 是通用配置。精简重置通知是个人偏好，去掉了上游有意展示的信息。

## 4. 设备码轮询遇到网络错误时重试

提交：[`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`5b5ab87295`](https://github.com/Adonis0123/hermes-agent/commit/5b5ab87295138a6bbd2af8a072a6c8897b2f3e7c)

**作用。** xAI Grok OAuth 和 Nous 登录共用一个 RFC 8628 轮询函数（`_poll_device_token_generic`）。
现在它遇到 `httpx.TransportError` 时按 `authorization_pending` 处理：等一个间隔再轮询，直到验证码过期。
以前只要一次 TLS 握手被断开（`SSL: UNEXPECTED_EOF_WHILE_READING`，连 `auth.x.ai` 时出现过），
用户还在授权，登录就结束了。

**文件。** `hermes_cli/auth_device_flow.py`、`tests/hermes_cli/test_auth_nous_provider.py`

**配置项。** 无。

**能否提交上游？** 可以。改动小，不针对某个 provider。

## 5. 失败轮提示跟随 `display.language`

提交：[`a24d61033e`](https://github.com/Adonis0123/hermes-agent/commit/a24d61033e12f6abcb10a06ed9747516db495cb4), [`5b5ab87295`](https://github.com/Adonis0123/hermes-agent/commit/5b5ab87295138a6bbd2af8a072a6c8897b2f3e7c)

**作用。** 失败轮结尾那条助手边界消息（"Your request was not processed…" / "This turn did not complete…"）
现在按 `agent.i18n.get_language()` 选语言（`HERMES_LANGUAGE` > `display.language` > `en`）。
中文（`zh`）有自己的文案，其他语言仍用英文。`untyped_failed_turn_display_kind` 能认出任一语言的提示，
旧消息照样按边界处理。英文常量和 `failed_turn_notice(turn_messages)` 保持兼容，新增可选参数 `lang=`。

**文件。** `agent/turn_failure_copy.py`、`gateway/run_turn_persistence.py`（v0.22.0 从 `gateway/run_turn.py` 拆出）、
`tests/agent/test_turn_failure_copy_language.py`

**配置项。** 无新增，读取 `display.language`。

**能否提交上游？** 可以。改动小，属于 i18n 扩展；上游可能希望把文案挪进 `locales/*.yaml`。

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
git log --oneline "$old_base"..pre-sync   # 每个叠层提交都要在 range-diff 里找到去向
scripts/run_tests.sh tests/gateway/test_feishu.py -q   # 补丁 2 列出的 3 条是 skip，不是失败
scripts/run_tests.sh tests/gateway/test_feishu_overlay_contracts.py \
  tests/gateway/test_feishu_stream_card.py tests/gateway/test_reset_display_tips.py \
  tests/gateway/test_session_info.py tests/hermes_cli/test_display_tips.py \
  tests/gateway/test_feishu_native_tables.py tests/agent/test_turn_failure_copy_language.py \
  tests/hermes_cli/test_auth_nous_provider.py -q
hermes feishu send --help >/dev/null   # 脚本靠这个插件 CLI 发消息
BASE=$(git describe --tags --abbrev=0 upstream-main)   # 例如 v2026.9.21
TAG="$BASE-adonis.<n>"; echo "$TAG"   # 同一上游 tag 下每次同步 n = 1, 2, ...
git tag "$TAG"
git push origin upstream-main
git push --force-with-lease origin main
git push origin "$TAG"
```

新上游里找不到的代码，可能是没重放的叠层：先对照上面 `git log` 列出的提交，再下「上游删了」的结论。
补丁冲突时，先重读上游新代码，保留补丁原意；补丁的文件或配置项变了，同步更新本文对应小节。

## 使用本 fork 时如何获取更新

clone 本 fork，让 `origin` 指向它。`hermes update` 快进到 `origin/main`；历史分叉时（每次 rebase 加强推同步后都会分叉）
直接重置到 `origin/main`。
因为 `main` 带着上游没有的提交，更新程序的上游检查（在你允许它添加 `upstream` remote 之后）会输出
"Skipping upstream sync to preserve your changes"，不会直接拉 NousResearch；上游改动随本 fork 的同步到达。
未提交的本地改动会在更新前后自动 stash。自己的提交请放在单独分支：在自定义分支上，更新程序会合并 `origin/main` 而不是重置。

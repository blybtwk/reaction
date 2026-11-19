# ComfyUI Reaction Context

用于 BA 的 Reaction 链路；Python 标准库实现，无依赖安装、无网络调用。将本目录作为插件仓库根目录，在 BA 安装后重启加载。没有在本地 ComfyUI 安装。

## ReactionContextBuilder

输入 `messages_json` 是按时间排列的数组，每条包含 `sender`、带时区的 `time`、`text` 或 `image: true`。`operator` 标识回应者，`locale` 决定输出语言。

- `trigger_time` 推荐测试时明确填写 ISO 8601 时间；为空时使用最后消息的时间（不加一分钟），`info.trigger_time_source=last_message`。
- 所有输入时间必须含时区，统一转换 UTC；只使用不晚于 trigger 且不早于 trigger 减 `max_hours` 的消息（边界包含）。
- 自动选择 trigger 之前最近一张非 operator 发的图；目标过期则不推荐，返回空文本和空图片，并在 info 说明原因。无手动目标参数；trigger 固定为 reaction。
- 目标之后超过 `max_after` 条消息时，info 标记不推荐，但仍输出供测试的窗口。生产流程应根据 recommend 控制入口和模型调用。
- 窗口最多 `max_context` 条，包含目标图，优先紧随目标的消息，不足向前补。
- 图片输入 `src_image_1` 至 `src_image_5` 按整个输入对话中图片出现顺序绑定，全部图片必须接满；缺图或超过 5 张报错，包括窗口外图片。节点无法验证实际图像内容与消息是否对应，由调用方保证顺序。
- 目标图及邻近图片最多取 `max_images` 张，按消息顺序编号。距离使用消息位置距离，相同距离优先后面的图片。

输出：`planner_input`、`creator_context`、`target_index`（1 开始）、`user_image_1`–`3`、`target_image`、`info`。少于三张时多余图片输出保持 `None`，不生成占位图。单图 BA 兼容性尚须实测，不能把本地纯逻辑通过视作模型兼容性通过。

Planner 图片端口接 user_image_1–3，Creator 和 Renderer 仅接 target_image。模型下游统一使用 `pre_llm_result`。

## 通用备用节点

`ReactionJSONExtract`：输入严格合法 JSON 和点分隔路径，例如 `context`、`recommend_prompt`、`recommend_prompt.0.intent`、`finalprompt`、`replies`、`technique`。字符串直接输出；对象、数组、数字、布尔值、null 序列化为合法 JSON。空路径返回整个值，缺失路径报错。不自动修改或修补模型输出。

`ReactionOptionSelector`：输入 recommend_prompt 数组（三项 intent 分别是 hype、tease、twist，每项含字符串 title、prompt）。可指定 intent 或选 random。相同 seed 和输入得到相同随机结果，输出完整选项 JSON。每次想重新随机时修改 seed。

这些节点作为 BA 无合适通用节点时的备用，不表示已经确认 BA 缺少对应能力。

## 验证

运行 `python3 -m unittest discover -s tests -v`。测试包括单图空输出、缺图报错、超过五张、UTC 转换、时间边界、未来消息排除、过期目标、自动目标、JSON 提取和确定性选择。必须另外在 BA 将空输出实际接到 Planner 并运行单图 case。

## 2026-11-19：端上已过滤的新协议

新增可选输入 `context_mode`：`legacy`（默认，旧 workflow 兼容）或 `upstream_filtered`（新 workflow 必须显式设置）。不改变原输出名称或索引。

`upstream_filtered` 信任端上已完成资格和时间窗口处理：不再按 5 小时、目标图后消息数量或 max_context 截断，也不按 trigger_time 排除消息。保留传入的全部聊天和摘要，最多 3 张真实图片全部按时间顺序编号；旧 max_images 值不会把它们裁掉。仍校验带时区时间戳和顺序、图片数不超过 3、真实图按出现顺序接满。目标图仍是输入中最近一张非 operator 发出的真实图片；摘要不会成为目标图。找不到真实目标图时返回空输出及 reason；`recommend` 是兼容字段，不表示重新执行了端上推荐规则。

源图片输入仍兼容 src_image_1–5，但新协议只用前 3 个；缺少的 user_image 槽位保持 None，目标图来自同一个计算结果。trigger_time 仍用于记录和展示；为空时按最后消息时间，info 标注来源。info 新增 context_mode。

历史图片摘要可以直接作为普通 `text` 消息，或者使用明确的 `image_summary` 字符串字段；摘要消息**不要**标记 `image: true`。只有真实附图消息才写 `image: true` 并连接实际图片。摘要不消耗槽位、不要求原图。附图同条的 text 和 image_summary 也会保留。

```json
[
  {"sender":"mia","time":"2026-11-18T09:00:00Z","image_summary":"更早的照片里是一只坐在窗边的橘猫。"},
  {"sender":"mia","time":"2026-11-19T10:00:00Z","image":true,"text":"今天这个状态怎么样？"},
  {"sender":"lily","time":"2026-11-19T10:01:00Z","text":"让我想个回应"}
]
```

本次只运行新协议测试 `python3 -m unittest discover -s tests -p test_upstream_filtered.py -v`：8 项通过，覆盖摘要保留与不占槽位、三图顺序及目标身份、缺图和超 3 图错误、单图空输出、附图文本保留、旧默认兼容与非法模式。没有重新做旧 5h/后4条资格过滤测试。此结果为纯 Python 逻辑验证，不代表 BA 已部署新版。

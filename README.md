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

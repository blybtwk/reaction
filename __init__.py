"""
ComfyUI-Reaction-Context
Reaction 上下文处理节点：按固定规则定位目标图、截取上下文窗口、给图片编号，
输出 Planner (VLM1) 的输入文本和 Creator (VLM2) 的上下文文本，并把图片路由到槽位。
"""
import json
from datetime import datetime, timedelta, timezone
import random


def _parse_time(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 Z 或 +08:00")
    return dt.astimezone(timezone.utc)


def _fmt_time(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_reaction_context(messages, operator, locale, trigger_time="",
                           max_context=5, max_after=4,
                           max_images=3, max_hours=5.0, context_mode="legacy"):
    """纯逻辑部分，不依赖 ComfyUI，方便单独测试。

    messages: [{"sender": "mia", "time": "2026-11-18T10:00:00Z", "text": "..."},
               {"sender": "mia", "time": "...", "image": true}, ...]  按时间顺序
    返回 dict，包含 planner_input / creator_context / target_index / slot_map 等
    """
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages 必须是非空数组")
    if context_mode not in ("legacy", "upstream_filtered"):
        raise ValueError("不支持的 context_mode")
    upstream_filtered = context_mode == "upstream_filtered"
    if not upstream_filtered and (not 1 <= max_images <= 3 or max_context < 1 or max_after < 0 or max_hours <= 0):
        raise ValueError("窗口或图片数量参数无效")
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("sender"), str):
            raise ValueError("每条消息必须包含 sender 字符串和带时区的 time")
    times = [_parse_time(m["time"]) for m in messages]
    if times != sorted(times):
        raise ValueError("messages 必须按时间先后排列")
    trigger_time_source = "explicit" if trigger_time.strip() else "last_message"
    t_trigger = _parse_time(trigger_time) if trigger_time.strip() else times[-1]
    cutoff = t_trigger - timedelta(hours=max_hours)
    img_order = [i for i, m in enumerate(messages) if m.get("image")]
    if upstream_filtered and len(img_order) > 3:
        raise ValueError("upstream_filtered 最多支持 3 张真实图；更早图片请用 text 或 image_summary 传摘要，不设 image=true")
    src_index = {msg_i: k for k, msg_i in enumerate(img_order)}
    past = list(range(len(messages))) if upstream_filtered else [i for i, t in enumerate(times) if t <= t_trigger]
    cands = [i for i in past if messages[i].get("image") and messages[i]["sender"] != operator]
    base_info = {"context_mode": context_mode, "trigger_time": _fmt_time(t_trigger), "trigger_time_source": trigger_time_source}
    if not cands:
        return {**base_info, "recommend": False, "reason": "触发时间之前没有别人发的图"}
    target = cands[-1]
    if not upstream_filtered and times[target] < cutoff:
        return {**base_info, "recommend": False, "reason": "目标图已超出时间窗口"}
    after = len([i for i in past if i > target])
    recommend = upstream_filtered or after <= max_after
    reason = "ok" if recommend else f"目标图之后已有 {after} 条消息，超过 {max_after} 条，不显示入口"

    eligible = [i for i in past if times[i] >= cutoff]
    following = [i for i in eligible if i > target][:max_context - 1]
    remaining = max_context - 1 - len(following)
    preceding = [i for i in eligible if i < target]
    window = (preceding[-remaining:] if remaining else []) + [target] + following

    if upstream_filtered:
        # Upstream owns eligibility/windowing; preserve every supplied summary and image.
        window = list(range(len(messages)))
        max_images = 3

    # 3. 选图：目标图 + 离它最近的 max_images-1 张
    win_imgs = [i for i in window if messages[i].get("image")]
    others = sorted([i for i in win_imgs if i != target], key=lambda i: (abs(i - target), -i))
    chosen = sorted([target] + others[:max_images - 1])
    label = {msg_i: f"user_image_{n + 1}" for n, msg_i in enumerate(chosen)}
    target_index = chosen.index(target) + 1
    target_label = label[target]
    target_sender = messages[target]["sender"]

    now = _fmt_time(t_trigger)
    trigger = (f"[trigger] [{now}] reaction | operator={operator}(user) | "
               f"target=[{target_label}] | target_sender={target_sender}(user)")

    def render(only_target):
        lines = []
        for i in window:
            m = messages[i]
            head = f"[{_fmt_time(times[i])}] {m['sender']}(user): "
            if m.get("image"):
                if i in label and (not only_target or i == target):
                    lines.append(head + f"[{label[i]}]")
                else:
                    lines.append(head + "[image_omitted]")
            else:
                lines.append(head + json.dumps(m.get("text", ""), ensure_ascii=False))
            # Image summaries are text context, never image placeholders or target candidates.
            if "image_summary" in m:
                if not isinstance(m["image_summary"], str):
                    raise ValueError("image_summary 必须是字符串")
                lines.append(head + "[历史图片摘要] " + json.dumps(m["image_summary"], ensure_ascii=False))
            if m.get("image") and m.get("text"):
                lines.append(head + json.dumps(m["text"], ensure_ascii=False))
        return lines

    header = [f"[locale={locale}]", trigger, "以下是用户之间的聊天记录（按时间先后排列）："]
    planner_input = "\n".join(header + render(False) +
                              ["", "附带图片（与标签顺序一致）："] +
                              [f"[{label[i]}]" for i in chosen])
    creator_context = "\n".join(header + render(True))

    return {
        **base_info,
        "recommend": recommend,
        "reason": reason,
        "planner_input": planner_input,
        "creator_context": creator_context,
        "target_index": target_index,
        "target_label": target_label,
        # slot_map[n] = 第几张输入图（0 开始）放进 user_image_{n+1}
        "slot_map": [src_index[i] for i in chosen],
    }


class ReactionContextBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "messages_json": ("STRING", {"multiline": True, "default": EXAMPLE}),
                "operator": ("STRING", {"default": "lily"}),
                "locale": ("STRING", {"default": "en-AU"}),
                "max_context": ("INT", {"default": 5, "min": 1, "max": 20}),
                "max_after": ("INT", {"default": 4, "min": 0, "max": 20}),
                "max_images": ("INT", {"default": 3, "min": 1, "max": 3}),
                "max_hours": ("FLOAT", {"default": 5.0, "min": 0.1, "max": 72.0}),
            },
            "optional": {
                "context_mode": (["legacy", "upstream_filtered"], {"default": "legacy"}),
                "trigger_time": ("STRING", {"default": ""}),
                "src_image_1": ("IMAGE",), "src_image_2": ("IMAGE",), "src_image_3": ("IMAGE",),
                "src_image_4": ("IMAGE",), "src_image_5": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("planner_input", "creator_context", "target_index",
                    "user_image_1", "user_image_2", "user_image_3", "target_image", "info")
    FUNCTION = "run"
    CATEGORY = "Reaction"

    def run(self, messages_json, operator, locale, max_context,
            max_after, max_images, max_hours, trigger_time="", context_mode="legacy", **imgs):
        messages = json.loads(messages_json)
        if not isinstance(messages, list):
            raise ValueError("messages_json 必须是数组")
        image_count = sum(bool(m.get("image")) for m in messages if isinstance(m, dict))
        if context_mode == "upstream_filtered" and image_count > 3:
            raise ValueError("upstream_filtered 最多支持 3 张真实图；更早图片请传文字摘要")
        if image_count > 5:
            raise ValueError("对话最多支持 5 张源图，请先裁剪输入对话")
        for k in range(1, image_count + 1):
            if imgs.get(f"src_image_{k}") is None:
                raise ValueError(f"缺少 src_image_{k}：对话里的图必须按出现顺序接满")
        r = build_reaction_context(messages, operator, locale, trigger_time,
                                   max_context, max_after, max_images, max_hours, context_mode)
        if "planner_input" not in r:
            return ("", "", 0, None, None, None, None, json.dumps(r, ensure_ascii=False))

        src = [imgs.get(f"src_image_{k}") for k in range(1, 6)]
        slots = [src[k] if k < len(src) else None for k in r["slot_map"]]
        slots += [None] * (3 - len(slots))
        target_img = slots[r["target_index"] - 1]
        info = json.dumps({k: r[k] for k in ("context_mode", "recommend", "reason", "target_index",
                                             "target_label", "slot_map", "trigger_time", "trigger_time_source")}, ensure_ascii=False)
        return (r["planner_input"], r["creator_context"], r["target_index"],
                slots[0], slots[1], slots[2], target_img, info)


EXAMPLE = json.dumps([
    {"sender": "mia", "time": "2026-11-18T10:00:00Z", "image": True},
    {"sender": "lily", "time": "2026-11-18T10:01:00Z", "text": "哈哈哈"},
    {"sender": "mia", "time": "2026-11-18T10:02:00Z", "text": "再来一张"},
    {"sender": "mia", "time": "2026-11-18T10:03:00Z", "image": True},
    {"sender": "mia", "time": "2026-11-18T10:04:00Z", "text": "这张更好笑"},
], ensure_ascii=False, indent=2)

class ReactionJSONExtract:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"json_text": ("STRING", {"multiline": True}),
                             "path": ("STRING", {"default": "context"})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("value",)
    FUNCTION = "run"
    CATEGORY = "Reaction"

    def run(self, json_text, path):
        value = json.loads(json_text)
        # Dot-separated keys and numeric array indices: recommend_prompt.0.intent.
        for part in path.split(".") if path else []:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),)


class ReactionOptionSelector:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "recommend_prompt": ("STRING", {"multiline": True}),
            "selection": (["hype", "tease", "twist", "random"],),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("selected_option",)
    FUNCTION = "run"
    CATEGORY = "Reaction"

    def run(self, recommend_prompt, selection, seed):
        options = json.loads(recommend_prompt)
        if not isinstance(options, list) or len(options) != 3:
            raise ValueError("recommend_prompt 必须是含三个选项的数组")
        if any(not isinstance(o, dict) for o in options):
            raise ValueError("每个选项必须是对象")
        if {o.get("intent") for o in options} != {"hype", "tease", "twist"}:
            raise ValueError("选项必须分别包含 hype、tease、twist")
        if any(not isinstance(o.get(k), str) for o in options for k in ("title", "prompt")):
            raise ValueError("每个选项必须包含 title 和 prompt 字符串")
        if selection == "random":
            chosen = random.Random(seed).choice(options)
        elif selection in {"hype", "tease", "twist"}:
            chosen = next(o for o in options if o["intent"] == selection)
        else:
            raise ValueError("不支持的选择模式")
        return (json.dumps(chosen, ensure_ascii=False),)


NODE_CLASS_MAPPINGS = {"ReactionContextBuilder": ReactionContextBuilder,
                       "ReactionJSONExtract": ReactionJSONExtract,
                       "ReactionOptionSelector": ReactionOptionSelector}
NODE_DISPLAY_NAME_MAPPINGS = {"ReactionContextBuilder": "Reaction 上下文处理",
                              "ReactionJSONExtract": "Reaction JSON 字段提取",
                              "ReactionOptionSelector": "Reaction 回应选项选择"}

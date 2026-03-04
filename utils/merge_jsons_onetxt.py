#!/usr/bin/env python3
"""
预处理 PoC_Paper_154/fulltext 目录下的 txt 文件：
- 每个 txt 为 JSON 数组 [{"type":"xxx"}, ...]
- type 为 image 时只保留 img_caption
- type 为 text 时拼接 text 内容，每段前加换行符
"""

import json
import os
from pathlib import Path


FULLTEXT_DIR = "/mnt/dhwfile/raise/user/zhuyun/protein_data_pipeline/PoC_Paper_154/fulltext"
OUTPUT_DIR = "/mnt/dhwfile/raise/user/zhuyun/protein_data_pipeline/PoC_Paper_154/preprocessed"


def process_item(item: dict) -> tuple[str, bool] | None:
    """处理单个 JSON 项，返回 (内容, 是否需前空两行)，无内容则返回 None。"""
    t = item.get("type")
    if t == "image":
        caption = item.get("img_caption")
        if caption is None:
            return None
        if isinstance(caption, list):
            parts = [str(p).strip() for p in caption if p]
            content = "\n".join(parts) if parts else None
        else:
            content = str(caption).strip() or None
        if content is None:
            return None
        return (content, False)
    if t == "text":
        text = item.get("text")
        if text is None:
            return None
        content = str(text).strip() or None
        if content is None:
            return None
        # 存在 text_level 且为 1 时，该段前换行两次
        need_double_newline = item.get("text_level") == 1
        return (content, need_double_newline)
    return None


def process_file(txt_path: str) -> str:
    """读取一个 txt，解析 JSON，按规则拼接并返回结果字符串。"""
    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()
    if not raw:
        return ""
    data = json.loads(raw)
    if not isinstance(data, list):
        return ""
    parts = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out = process_item(item)
        if out:
            parts.append(out)
    if not parts:
        return ""
    # 首段不加换行，后续：text_level==1 则换行两次，否则换行一次
    result = parts[0][0]
    for content, need_double in parts[1:]:
        result += "\n\n" if need_double else "\n"
        result += content
    return result


def main():
    fulltext = Path(FULLTEXT_DIR)
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_files = sorted(fulltext.glob("*.txt"))
    for txt_path in txt_files:
        try:
            content = process_file(str(txt_path))
            out_path = out_dir / txt_path.name
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"OK: {txt_path.name}")
        except Exception as e:
            print(f"FAIL: {txt_path.name} -> {e}")


if __name__ == "__main__":
    main()

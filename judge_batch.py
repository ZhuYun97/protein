#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI


SYSTEM_PROMPT = """你是“论文事实一致性评审器（LLM-as-Judge）”。
你只依据输入的原论文证据进行评分，不使用外部知识。
不输出思维链，只输出严格 JSON。"""


USER_PROMPT_TEMPLATE = """任务：对给定论文的 `02_agent_trajectory` 中每个 step 做事实一致性评分（满分 5 分），只依据输入的原论文文本证据。

【输入】
1) paper_id: {paper_id}
2) source_evidence_text:
{source_evidence_text}
3) extracted_result_json:
{extracted_result_json}

【硬性评审规则】
1. 只看“是否符合原论文事实”，不看文风。
2. 证据优先级：直接文本证据 > 图表标题/注释证据 > 间接上下文推断。
3. 若 claim 在原文找不到证据：记为 unsupported（不得给高分）。
4. 若 claim 与原文明显冲突（工具、参数、数值、结论方向错误）：记为 contradicted（重罚）。
5. 允许“轻微表述改写”，但不允许“新增事实”。
6. 不输出思维链，只输出结构化结论。

【逐步骤评分维度（总分 5）】
对每个 step 分4个维度打分，再求和：
A. 核心事实一致性（0-2）
- 2: 工具/方法/实验类型与原文一致，无关键错误
- 1: 主体基本一致，但有次要偏差
- 0: 关键事实错误或冲突

B. 参数与条件准确性（0-1.5）
- 1.5: 关键参数、阈值、样本量、配置均有证据
- 0.5-1.0: 部分准确，部分缺失或弱证据
- 0: 多数参数无证据或明显错误

C. 观察结果一致性（0-1）
- 1: observation 与原文结果一致
- 0.5: 方向对但细节缺失/泛化
- 0: 与原文不符或编造结果

D. 引用可追溯性（0-0.5）
- 0.5: references 可在文中定位且相关
- 0.25: 部分可定位
- 0: 不可定位或不相关

最终 step_score = A + B + C + D（保留 1 位小数，范围 0.0~5.0）

【分数解释标签】
- 4.5-5.0: fully_supported
- 3.5-4.4: mostly_supported
- 2.5-3.4: partially_supported
- 1.0-2.4: weakly_supported
- 0.0-0.9: unsupported_or_contradicted

【输出格式（严格 JSON）】
{{
  "paper_id": "...",
  "step_scores": [
    {{
      "step_index": 1,
      "score": 4.2,
      "label": "mostly_supported",
      "dimension_scores": {{
        "A_core_fact": 2.0,
        "B_parameters": 1.0,
        "C_observation": 1.0,
        "D_references": 0.2
      }},
      "evidence": [
        {{
          "supports_field": "tool|parameters|observation|references",
          "quote": "原文短引文",
          "page_idx": 3
        }}
      ],
      "issues": [
        {{
          "type": "unsupported|contradicted|missing",
          "field": "parameters.xxx",
          "claim": "被评估文本中的原句或关键短语",
          "reason": "为何无证据/冲突"
        }}
      ],
      "fix_suggestion": "如何改写该 step 才与原文一致"
    }}
  ],
  "summary": {{
    "num_steps": 0,
    "mean_score": 0.0,
    "median_score": 0.0,
    "critical_steps_below_2.5": [],
    "hallucination_risk": "low|medium|high"
  }}
}}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch judge extracted trajectory steps against source fulltext."
    )
    parser.add_argument(
        "--extracted-dir",
        default="extracted_outputs/PoC_Paper_154_CoTs_Qwen3.5",
        help="Directory containing extracted result json files.",
    )
    parser.add_argument(
        "--fulltext-dir",
        default="input_pdfs/PoC_Paper_154/fulltext",
        help="Directory containing source fulltext json-in-txt files.",
    )
    parser.add_argument(
        "--output-dir",
        default="judge_outputs/PoC_Paper_154_CoTs_Qwen3.5",
        help="Directory to write judge outputs.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("JUDGE_MODEL", "qwen3.5-397b-a17b"),
        help="Model name for OpenAI-compatible API.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("JUDGE_BASE_URL", "http://35.220.164.252:3888/v1/"),
        help="OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("API_KEY", "sk-1xhEtICdXAeRFsuhAKrCOdQpY3F5fdx5Jk86AtlmfHLb0tzl"),
        help="API key.",
    )
    parser.add_argument(
        "--max-evidence-chars",
        type=int,
        default=120000,
        help="Max characters of source evidence sent to the judge model.",
    )
    parser.add_argument(
        "--max-extracted-chars",
        type=int,
        default=60000,
        help="Max characters of extracted result json sent to the judge model.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per file.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Sleep between API calls to reduce rate-limit pressure.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process first N files (0 means all).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompts and validate inputs without API calls.",
    )
    return parser.parse_args()


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    print("-------------truncate fulltext----------len:", len(text))
    head = int(max_chars * 0.6)
    tail = max_chars - head
    return (
        text[:head]
        + "\n\n[...TRUNCATED...]\n\n"
        + text[-tail:]
    )


def build_evidence_text(fulltext_items: List[Dict[str, Any]], max_chars: int) -> str:
    parts: List[str] = []
    for item in fulltext_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        page_idx = item.get("page_idx", "?")

        if item_type == "text":
            txt = str(item.get("text", "")).strip()
            if txt:
                parts.append(f"[p{page_idx}] {txt}")
        elif item_type == "image":
            cap = item.get("img_caption", [])
            if isinstance(cap, list):
                cap_text = " ".join(str(x).strip() for x in cap if str(x).strip())
            else:
                cap_text = str(cap).strip()
            if cap_text:
                parts.append(f"[p{page_idx}] [FIGURE] {cap_text}")
        elif item_type == "table":
            cap = item.get("table_caption", [])
            if isinstance(cap, list):
                cap_text = " ".join(str(x).strip() for x in cap if str(x).strip())
            else:
                cap_text = str(cap).strip()
            body = str(item.get("table_body", "")).strip()
            body_short = body[:600] if body else ""
            if cap_text or body_short:
                parts.append(f"[p{page_idx}] [TABLE] {cap_text} {body_short}".strip())

    merged = "\n".join(parts)
    return truncate_text(merged, max_chars=max_chars)


def extract_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"json", "jsonc"}:
            text = "\n".join(lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def llm_judge(
    client: OpenAI,
    model: str,
    paper_id: str,
    evidence_text: str,
    extracted_json_str: str,
    temperature: float,
    max_retries: int,
) -> Dict[str, Any]:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        paper_id=paper_id,
        source_evidence_text=evidence_text,
        extracted_result_json=extracted_json_str,
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                )

            content = resp.choices[0].message.content or "{}"
            return extract_json_from_text(content)
        except Exception as exc:
            last_error = exc
            print(f"[WARN] {paper_id} attempt {attempt}/{max_retries} failed: {exc}")
            if attempt < max_retries:
                time.sleep(min(2 * attempt, 8))

    raise RuntimeError(f"Judge failed after retries for {paper_id}: {last_error}")


def main() -> None:
    args = parse_args()

    extracted_dir = Path(args.extracted_dir)
    fulltext_dir = Path(args.fulltext_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(extracted_dir.glob("*.json"))
    if args.limit > 0:
        files = files[: args.limit]

    if not files:
        raise SystemExit(f"No json files found in {extracted_dir}")

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    manifest: List[Dict[str, Any]] = []

    print(f"[INFO] total files: {len(files)}")
    print(f"[INFO] output dir: {output_dir}")
    if args.dry_run:
        print("[INFO] dry-run mode enabled, no API calls.")

    for i, ext_path in enumerate(files, start=1):
        paper_id = ext_path.stem
        fulltext_path = fulltext_dir / f"{paper_id}.txt"
        out_path = output_dir / f"{paper_id}.judge.json"

        if out_path.exists() and not args.overwrite:
            print(f"[{i}/{len(files)}] skip existing: {paper_id}")
            manifest.append(
                {"paper_id": paper_id, "status": "skipped_existing", "output": str(out_path)}
            )
            continue

        if not fulltext_path.exists():
            print(f"[{i}/{len(files)}] missing fulltext: {paper_id}")
            manifest.append(
                {"paper_id": paper_id, "status": "missing_fulltext", "output": str(out_path)}
            )
            continue

        try:
            extracted_obj = json.loads(ext_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[{i}/{len(files)}] invalid extracted json: {paper_id} -> {exc}")
            manifest.append(
                {"paper_id": paper_id, "status": "invalid_extracted_json", "error": str(exc)}
            )
            continue

        try:
            fulltext_obj = json.loads(fulltext_path.read_text(encoding="utf-8"))
            if not isinstance(fulltext_obj, list):
                raise ValueError("fulltext root is not list")
        except Exception as exc:
            print(f"[{i}/{len(files)}] invalid fulltext json: {paper_id} -> {exc}")
            manifest.append(
                {"paper_id": paper_id, "status": "invalid_fulltext_json", "error": str(exc)}
            )
            continue

        evidence_text = build_evidence_text(
            fulltext_obj, max_chars=args.max_evidence_chars
        )
        extracted_json_str = truncate_text(
            json.dumps(extracted_obj, ensure_ascii=False, indent=2),
            max_chars=args.max_extracted_chars,
        )

        if args.dry_run:
            dry_payload = {
                "paper_id": paper_id,
                "evidence_chars": len(evidence_text),
                "extracted_chars": len(extracted_json_str),
                "status": "dry_run_ok",
            }
            out_path.write_text(
                json.dumps(dry_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[{i}/{len(files)}] dry-run ok: {paper_id}")
            manifest.append({"paper_id": paper_id, "status": "dry_run_ok"})
            continue

        print(f"[{i}/{len(files)}] judging: {paper_id}")
        start = time.time()
        try:
            judge_result = llm_judge(
                client=client,
                model=args.model,
                paper_id=paper_id,
                evidence_text=evidence_text,
                extracted_json_str=extracted_json_str,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )
            elapsed = time.time() - start

            wrapped = {
                "paper_id": paper_id,
                "model": args.model,
                "elapsed_seconds": round(elapsed, 2),
                "judge_result": judge_result,
            }
            out_path.write_text(
                json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[{i}/{len(files)}] done: {paper_id} ({elapsed:.1f}s)")
            manifest.append(
                {
                    "paper_id": paper_id,
                    "status": "ok",
                    "elapsed_seconds": round(elapsed, 2),
                    "output": str(out_path),
                }
            )
        except Exception as exc:
            elapsed = time.time() - start
            print(f"[{i}/{len(files)}] failed: {paper_id} -> {exc}")
            manifest.append(
                {
                    "paper_id": paper_id,
                    "status": "failed",
                    "elapsed_seconds": round(elapsed, 2),
                    "error": str(exc),
                }
            )

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "total": len(files),
                "ok": sum(1 for x in manifest if x.get("status") == "ok"),
                "failed": sum(1 for x in manifest if x.get("status") == "failed"),
                "skipped_existing": sum(
                    1 for x in manifest if x.get("status") == "skipped_existing"
                ),
                "dry_run_ok": sum(1 for x in manifest if x.get("status") == "dry_run_ok"),
                "items": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[INFO] manifest saved to: {manifest_path}")


if __name__ == "__main__":
    main()

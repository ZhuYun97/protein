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


USER_PROMPT_TEMPLATE = """任务：对给定论文抽取结果中的三个部分分别做事实一致性评分（满分均为 5 分），只依据输入的原论文文本证据：
- `01_initial_request`
- `02_agent_trajectory`
- `03_success_verification`

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
6. `01_initial_request` 和 `03_success_verification` 必须分别独立打分，不能并入 step 平均分。
7. 不输出思维链，只输出结构化结论。

【01_initial_request 评分维度（总分 5）】
对 `01_initial_request` 的 4 个维度打分，再求和：
A. 目标产物准确性（0-1.5）
- 1.5: `target_name` 与论文研究对象一致，命名准确且无关键扩写
- 0.5-1.0: 主体对象基本对，但表述偏泛或夹带少量弱证据信息
- 0: 目标对象错误、混淆或明显编造

B. 输入数据/起始材料准确性（0-1.5）
- 1.5: `input_data` 清楚对应原文起始序列、数据库、样本、底物或材料
- 0.5-1.0: 只覆盖部分输入，或混入少量推断
- 0: 关键起始材料无证据或与原文冲突

C. 研究意图忠实度（0-1.0）
- 1.0: `user_intent` 准确概括研究动机/需求，不夸大
- 0.5: 方向基本正确，但抽象过度或遗漏关键限定条件
- 0: 动机方向错误或加入原文未表达的目的

D. 可量化目标严谨性（0-1.0）
- 1.0: `quantifiable_goal` 中的指标、方向或阈值有证据支持
- 0.5: 只支持定性方向，量化程度不足但未硬编数值
- 0: 编造具体指标/数值，或把实验结果误写成初始目标

最终 `initial_request_score = A + B + C + D`（保留 1 位小数，范围 0.0~5.0）

【02_agent_trajectory 逐步骤评分维度（总分 5）】
对 `02_agent_trajectory` 中每个 step 分 4 个维度打分，再求和：
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

【03_success_verification 评分维度（总分 5）】
对 `03_success_verification` 的 3 个维度打分，再求和：
A. 验证方式一致性（0-1.5）
- 1.5: `validation_technique` 与论文用于验证/验收的方法一致
- 0.5-1.0: 方向基本正确，但方法名过泛、混合了多个层次或缺少关键限定
- 0: 验证方法错误或无证据

B. 指标准确性与完整性（0-2.0）
- 2.0: `metrics` 中关键指标的名称、值、单位、解释均有证据
- 1.0-1.5: 主要指标方向正确，但部分值/单位/解释缺失或证据偏弱
- 0.5: 仅有零散定性指标或混入弱推断
- 0: 大量指标无证据、数值错误或明显编造

C. 最终结论一致性（0-1.5）
- 1.5: `final_verdict` 与论文最终结论/claim 强一致
- 0.5-1.0: 大方向正确，但措辞比原文更强或更弱
- 0: 与原文结论冲突，或把局部结果夸大为整体成功

最终 `success_verification_score = A + B + C`（保留 1 位小数，范围 0.0~5.0）

【分数解释标签】
- 4.5-5.0: fully_supported
- 3.5-4.4: mostly_supported
- 2.5-3.4: partially_supported
- 1.0-2.4: weakly_supported
- 0.0-0.9: unsupported_or_contradicted

【输出格式（严格 JSON）】
{{
  "paper_id": "...",
  "initial_request_score": {{
    "score": 4.2,
    "label": "mostly_supported",
    "dimension_scores": {{
      "A_target_name": 1.5,
      "B_input_data": 1.2,
      "C_user_intent": 0.8,
      "D_quantifiable_goal": 0.7
    }},
    "evidence": [
      {{
        "supports_field": "target_name|input_data|user_intent|quantifiable_goal",
        "quote": "原文短引文",
        "page_idx": 1
      }}
    ],
    "issues": [
      {{
        "type": "unsupported|contradicted|missing",
        "field": "quantifiable_goal",
        "claim": "被评估文本中的原句或关键短语",
        "reason": "为何无证据/冲突"
      }}
    ],
    "fix_suggestion": "如何改写 01_initial_request 才与原文一致"
  }},
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
  "success_verification_score": {{
    "score": 4.6,
    "label": "fully_supported",
    "dimension_scores": {{
      "A_validation_technique": 1.5,
      "B_metrics": 1.8,
      "C_final_verdict": 1.3
    }},
    "evidence": [
      {{
        "supports_field": "validation_technique|metrics.xxx|final_verdict",
        "quote": "原文短引文",
        "page_idx": 8
      }}
    ],
    "issues": [
      {{
        "type": "unsupported|contradicted|missing",
        "field": "metrics.kcat_over_km.value|final_verdict",
        "claim": "被评估文本中的原句或关键短语",
        "reason": "为何无证据/冲突"
      }}
    ],
    "fix_suggestion": "如何改写 03_success_verification 才与原文一致"
  }},
  "summary": {{
    "num_steps": 0,
    "mean_score": 0.0,
    "median_score": 0.0,
    "critical_steps_below_2.5": [],
    "initial_request_score": 0.0,
    "success_verification_score": 0.0,
    "overall_score": 0.0,
    "hallucination_risk": "low|medium|high"
  }}
}}

【summary 字段填写要求】
- `num_steps`: `step_scores` 的数量。
- `mean_score`: 所有 step `score` 的平均值；若无 step 则为 0.0。
- `median_score`: 所有 step `score` 的中位数；若无 step 则为 0.0。
- `critical_steps_below_2.5`: 所有 `score < 2.5` 的 step_index 列表。
- `initial_request_score`: 直接填写上面的 `initial_request_score.score`。
- `success_verification_score`: 直接填写上面的 `success_verification_score.score`。
- `overall_score`: 计算 `(initial_request_score + mean_score + success_verification_score) / 3`，保留 1 位小数。
- `hallucination_risk`: 若 01/03 任一部分低于 2.5，或存在多个低于 2.5 的 step，则倾向 `high`；若有少量弱证据问题则为 `medium`；整体证据充分则为 `low`。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch judge extracted initial request, trajectory, and verification against source fulltext."
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

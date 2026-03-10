#!/usr/bin/env python3
import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

from judge_batch import build_evidence_text, llm_judge, truncate_text


_thread_local = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Threaded batch judge for extracted trajectory steps."
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
        help="Sleep before each API call in a worker.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process first N files (0 means all).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of worker threads.",
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


def get_thread_client(base_url: str, api_key: str) -> OpenAI:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = OpenAI(base_url=base_url, api_key=api_key)
        _thread_local.client = client
    return client


def process_one_file(ext_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    paper_id = ext_path.stem
    fulltext_path = Path(args.fulltext_dir) / f"{paper_id}.txt"
    out_path = Path(args.output_dir) / f"{paper_id}.judge.json"

    if out_path.exists() and not args.overwrite:
        return {"paper_id": paper_id, "status": "skipped_existing", "output": str(out_path)}

    if not fulltext_path.exists():
        return {"paper_id": paper_id, "status": "missing_fulltext", "output": str(out_path)}

    try:
        extracted_obj = json.loads(ext_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"paper_id": paper_id, "status": "invalid_extracted_json", "error": str(exc)}

    try:
        fulltext_obj = json.loads(fulltext_path.read_text(encoding="utf-8"))
        if not isinstance(fulltext_obj, list):
            raise ValueError("fulltext root is not list")
    except Exception as exc:
        return {"paper_id": paper_id, "status": "invalid_fulltext_json", "error": str(exc)}

    evidence_text = build_evidence_text(fulltext_obj, max_chars=args.max_evidence_chars)
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
        out_path.write_text(json.dumps(dry_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"paper_id": paper_id, "status": "dry_run_ok", "output": str(out_path)}

    if args.sleep_seconds > 0:
        time.sleep(args.sleep_seconds)

    start = time.time()
    try:
        client = get_thread_client(args.base_url, args.api_key)
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
        out_path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "paper_id": paper_id,
            "status": "ok",
            "elapsed_seconds": round(elapsed, 2),
            "output": str(out_path),
        }
    except Exception as exc:
        elapsed = time.time() - start
        return {
            "paper_id": paper_id,
            "status": "failed",
            "elapsed_seconds": round(elapsed, 2),
            "error": str(exc),
        }


def main() -> None:
    args = parse_args()
    extracted_dir = Path(args.extracted_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(extracted_dir.glob("*.json"))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"No json files found in {extracted_dir}")

    print(f"[INFO] total files: {len(files)}")
    print(f"[INFO] workers: {args.workers}")
    print(f"[INFO] output dir: {output_dir}")
    if args.dry_run:
        print("[INFO] dry-run mode enabled, no API calls.")

    manifest: List[Dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_file = {executor.submit(process_one_file, f, args): f for f in files}
        for fut in as_completed(future_to_file):
            done += 1
            ext_path = future_to_file[fut]
            paper_id = ext_path.stem
            try:
                item = fut.result()
            except Exception as exc:
                item = {"paper_id": paper_id, "status": "failed", "error": str(exc)}

            manifest.append(item)
            status = item.get("status", "unknown")
            if status == "ok":
                print(f"[{done}/{len(files)}] done: {paper_id}")
            elif status in {"dry_run_ok", "skipped_existing"}:
                print(f"[{done}/{len(files)}] {status}: {paper_id}")
            else:
                print(f"[{done}/{len(files)}] {status}: {paper_id} -> {item.get('error', '')}")

    manifest.sort(key=lambda x: x.get("paper_id", ""))
    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "total": len(files),
                "workers": args.workers,
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

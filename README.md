# Protein Data Pipeline

蛋白质/酶相关论文数据处理与信息抽取流水线：从 PDF 解析、文本预处理到结构化信息抽取（目标产物、实验轨迹、工具参数、验证结果等）。

---

## 目录结构概览

- **根目录**：主流程脚本（解析、分块、抽取）
- **utils/**：通用工具与预处理
- **input_pdfs/**：输入 PDF（略）
- **parsed_outputs/**：解析后的 Markdown/JSON 结果
- **extracted_outputs/**：大模型抽取后的结构化 JSON 结果
- **paddlevl_env/**：PaddleOCR 等环境相关（略）
- **logs/**：日志（略）

---

## 各文件功能说明

### 根目录脚本

| 文件 | 功能 |
|------|------|
| **extract_EN.py** | **英文论文信息抽取**。调用 OpenAI 兼容 API（如 Qwen），从预处理后的论文文本中分层抽取：`01_initial_request`（目标产物、输入数据、用户意图、可量化目标）、`02_agent_trajectory`（步骤中的 thought、action、tool、parameters、observation、valid、references）、`03_success_verification`（验证方式、指标、最终结论）。支持单文件或目录批量处理，可配置并发数。依赖 `utils.extract_paper_sections` 做摘要/引言与 Methods 的切分。 |
| **extract_CN.py** | **中文版论文抽取**。与 `extract_EN.py` 逻辑一致，但 Pydantic Schema 与 prompt 为中文，用于从中文论文中抽取相同结构的初始请求、实验轨迹与成功验证信息。 |
| **judge_batch.py** | **串行事实一致性评测**。读取 `extracted_outputs` 中的结构化 JSON 与 `fulltext` 原文证据，调用 OpenAI 兼容 API 对 `01_initial_request`、`02_agent_trajectory`、`03_success_verification` 三部分分别打分，并输出逐文件 `.judge.json` 与 `_manifest.json`。 |
| **judge_batch_threaded.py** | **并行事实一致性评测**。与 `judge_batch.py` 使用同一套评审 prompt 和输出格式，但通过多线程并发处理多篇论文，适合大规模批量打分。 |
| **chunking.py** | **Markdown 分块与工具上下文检索**。使用 LangChain：先按 Markdown 标题（# / ## / ###）切分并保留章节元数据，再按字符滑动窗口（chunk_size=500, overlap=50）二次切分；提供 `retrieve_tool_context(tool_name, chunks, window_size)`，按工具名检索相关 chunks 并扩展前后相邻块，供后续抽取使用。面向 MinerU 解析得到的 Markdown 文本。 |
| **paddle_parser.py** | **PDF 解析入口**。使用 PaddleOCRVL 对指定 PDF 进行版面分析与表格/标题识别，支持：合并跨页表格、重建多级标题、多页合并为单页。将结果保存为 JSON 与 Markdown 到 `parsed_outputs/paddlevl1.5/` 下对应案例目录。 |

### utils 工具模块

| 文件 | 功能 |
|------|------|
| **utils/__init__.py** | 包入口，对外导出 `extract_paper_sections`，供 `extract_EN.py` 等调用。 |
| **utils/split_abs_method.py** | **摘要/引言与 Methods 切分**。通过正则从论文全文中抽取：Abstract、Introduction（或仅其一），以及从 Methods 起始到 References 之前的 `methods_text`。用于在抽取前把「摘要+引言」与「方法」分开处理，保证 pipeline 输入格式一致。 |
| **utils/merge_jsons_onetxt.py** | **Fulltext JSON 转纯文本**。针对 `PoC_Paper_154/fulltext` 下每个 txt（内容为 JSON 数组 `[{"type":"xxx"}, ...]`）：`type=image` 时只保留 `img_caption`，`type=text` 时拼接 `text`，并按 `text_level` 控制段前换行。输出到 `PoC_Paper_154/preprocessed`，供后续 `extract_EN` / `extract_CN` 读取。 |

### 说明文档

| 文件 | 功能 |
|------|------|
| **explain.txt** | 抽取字段说明：Initial Request（target_name、input_data、user_intent、quantifiable_goal）、TrajectoryStep（thought、action、tool、parameters、observation、valid、references）、SuccessVerification（validation_technique、metrics、final_verdict）等格式与填写要求。 |

---

## 使用说明摘要

- **解析 PDF**：修改 `paddle_parser.py` 中 `input_file` 与输出路径后运行，得到 `parsed_outputs` 下的 JSON/MD。
- **预处理 fulltext**：运行 `python utils/merge_jsons_onetxt.py`，将 fulltext 下的 txt 转为 preprocessed 纯文本。
- **英文抽取**：`python extract_EN.py [输入路径] [并发数]`，输入可为单文件或目录，默认使用预配置的 preprocessed 目录与输出目录。
- **中文抽取**：运行 `extract_CN.py` 中 `__main__` 逻辑，传入对应摘要/引言与 Methods 文本。
- **串行 judge**：`python judge_batch.py --extracted-dir extracted_outputs/PoC_Paper_154_CoTs_Qwen3.5 --fulltext-dir input_pdfs/PoC_Paper_154/fulltext --output-dir judge_outputs/PoC_Paper_154_CoTs_Qwen3.5`。会对每篇论文的 `01_initial_request`、`02_agent_trajectory`、`03_success_verification` 分别做事实一致性评分，输出单篇 `.judge.json` 和汇总 `_manifest.json`。
- **并行 judge**：`python judge_batch_threaded.py --workers 6 --extracted-dir extracted_outputs/PoC_Paper_154_CoTs_Qwen3.5 --fulltext-dir input_pdfs/PoC_Paper_154/fulltext --output-dir judge_outputs/PoC_Paper_154_CoTs_Qwen3.5`。适合大批量打分，`--workers` 控制并发数。
- **judge 常用参数**：`--model`、`--base-url`、`--api-key` 用于切换评审模型与接口；`--max-evidence-chars`、`--max-extracted-chars` 控制送入模型的证据长度；`--limit` 可只评前 N 篇；`--overwrite` 可覆盖已有输出；`--sleep-seconds` 用于限速。
- **judge dry-run**：`python judge_batch.py --dry-run --limit 1`。只构造输入并检查文件可读性，不发起 API 请求，适合先验证目录配置是否正确。
- **judge 输出格式**：每个输出文件包含 `initial_request_score`、`step_scores`、`success_verification_score` 和 `summary`。其中 `summary.overall_score` 是 `01` 分数、`02` 步骤均分、`03` 分数的平均值。
- **分块与检索**：在需要按工具名检索上下文的流程中，先对 MinerU Markdown 调用 `chunk_texts_with_langchain`，再对得到的 chunks 调用 `retrieve_tool_context(tool_name, chunks)`。

输入文件（如 PDF、原始 txt）、环境目录（env）与日志目录（logs）未在本文中逐一列出。

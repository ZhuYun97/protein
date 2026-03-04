"""工具函数，供 pipeline 其他模块调用。"""
import re

# 预编译正则，避免每个文件重复编译
_RE_PAGE = re.compile(r'\n\s*\d+\s*\n')
_RE_FULL = re.compile(
    r"(?is)Abstract\s*(.*?)\s*Introduction\s*(.*?)(?=\n\s*(?:2\.|Related|Methods|Methodology)|\n\n)"
)
_RE_INTRO_ONLY = re.compile(
    r"(?is)Introduction\s*(.*?)(?=\n\s*(?:2\.|Related|Methods|Methodology)|\n\n)"
)
_RE_ABSTRACT_ONLY = re.compile(r"(?is)Abstract\s*(.*?)\n\n")
_RE_KEYWORDS = re.compile(r"(?i)\s*Keywords\s*:")  # 只做定位，用切片取内容
_RE_REFERENCES = re.compile(r'(?i)\n\s*(?:References|Bibliography)')


def extract_paper_sections(text):
    """
    从论文全文中抽取摘要/引言部分与 Methods 部分。

    Args:
        text: 论文全文。

    Returns:
        (extracted_content, methods_text): 摘要/引言等内容，以及 Methods 部分文本。
    """
    # 预处理：统一换行符，去除页码干扰（可选）
    clean_text = _RE_PAGE.sub('\n', text)

    match_full = _RE_FULL.search(clean_text)
    if match_full:
        # 成功获取 Abstract + Intro
        extracted_content = f"Abstract {match_full.group(1)} Introduction {match_full.group(2)}"
        methods_start_index = match_full.end()
    else:
        match_intro = _RE_INTRO_ONLY.search(clean_text)
        if match_intro:
            extracted_content = f"Introduction {match_intro.group(1)}"
            methods_start_index = match_intro.end()
        else:
            match_abstract = _RE_ABSTRACT_ONLY.search(clean_text)
            if match_abstract:
                extracted_content = f"Abstract {match_abstract.group(1).strip()}"
                methods_start_index = match_abstract.end()
            else:
                # Keywords: 之前的内容（用定位+切片，避免 (.*?) 回溯）
                match_kw = _RE_KEYWORDS.search(clean_text)
                if match_kw:
                    extracted_content = clean_text[: match_kw.start()].strip()
                    methods_start_index = match_kw.end()
                else:
                    # 都匹配不到时，用全文作为 extracted_content
                    extracted_content = _RE_REFERENCES.split(clean_text)[0]
                    methods_start_index = 0

    # 获取 methods_text：从匹配结束点一直到文章末尾（或到 References 之前）
    methods_text = clean_text[methods_start_index:].strip()
    # methods_text = _RE_REFERENCES.split(methods_text)[0]

    return extracted_content, methods_text

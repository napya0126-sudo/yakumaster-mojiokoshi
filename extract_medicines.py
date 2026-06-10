#!/usr/bin/env python3
"""
内服薬マスタPDFから医薬品情報を抽出してCSVに出力するスクリプト

出力列: 医薬品名, 効能, 注意事項, 使用方法, 保存方法
"""

import re
import csv
import subprocess
from pathlib import Path

PDF_DIR = Path(__file__).parent


def _pdf_sort_key(p: Path) -> int:
    m = re.search(r'_(\d+)', p.name)
    return int(m.group(1)) if m else 0


PDF_FILES = sorted(PDF_DIR.glob("内服薬_*.pdf"), key=_pdf_sort_key)
OUTPUT_CSV = PDF_DIR / "内服薬_マスタ.csv"

HEADER_PATTERNS = [
    r'^\[10000-10\]$',
    r'^泉南動物病院 様',
    r'^調剤日：',
    r'^\(\d+ /\d+\)$',
]

FOOTER_PATTERNS = [
    r'^獣医師の指示のもと、用法用量を守ってお使いください。$',
    r'^別の動物や人体への使用は、絶対におやめください。$',
    r'^\d{3}-\d{4}',
    r'^TEL ',
    r'^http://',
    r'^診療時間',
    r'^休 診 日',
    r'^処方担当$',
    r'^泉南動物病院$',
]

DRUG_NAME_PATTERNS = [
    r'mg|ｍｇ|㎎|㎍',
    r'錠',
    r'カプセル|ｶﾌﾟｾﾙ',
    r'散$',
    r'液',
    r'シロップ',
    # mg/錠のないブランド名：純粋なカタカナのみ・短い行
    r'^[ァ-ヶーｦ-ﾟ]{3,15}$',
]

# 行全体がノイズである追加パターン（調剤数量表記など）
LINE_NOISE_PATTERNS = [
    r'^＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞$',
]

# 説明文中のインラインノイズ（文章の一部として現れる）
INLINE_NOISE_PATTERNS = [
    r'＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞',
]

DOSAGE_PATTERN = re.compile(r'^1回\s+')
FIELD_NAMES = ["医薬品名", "効能", "注意事項", "使用方法", "保存方法"]
CONTENT_FIELDS = ["効能", "注意事項", "使用方法", "保存方法"]


def is_noise(line: str) -> bool:
    return any(re.search(p, line) for p in HEADER_PATTERNS + FOOTER_PATTERNS + LINE_NOISE_PATTERNS)


def is_dosage(line: str) -> bool:
    return bool(DOSAGE_PATTERN.match(line))


def is_drug_name(line: str) -> bool:
    if not line or is_dosage(line) or is_noise(line):
        return False
    return any(re.search(p, line) for p in DRUG_NAME_PATTERNS)


def extract_text_from_pdf(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", str(pdf_path), "-"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed for {pdf_path}: {result.stderr}")
    return result.stdout


def classify_sentence(sentence: str) -> str | None:
    s = sentence.strip()
    if not s or is_noise(s):
        return None

    if re.search(r'保存', s):
        return "保存方法"
    if s.startswith("本剤投与後") or "誤食してしまう危険性" in s:
        return "注意事項"
    if s.startswith("このお薬は") or re.match(r'^(複合|抗|免疫|止血|不整脈|ペニシリン)', s):
        return "効能"
    if (
        s.startswith("獣医師")
        or "空腹時に与えて" in s
        or "投与を中止しない" in s
        or "与えて下さい" in s
        or "与えてください" in s
        or "ご使用" in s
    ):
        return "使用方法"
    if re.search(r'(剤|薬|治療|作用|特徴)', s) and not s.startswith("本剤"):
        return "効能"

    return None


def clean_japanese_spaces(text: str) -> str:
    # 調剤数量表記を除去
    for p in INLINE_NOISE_PATTERNS:
        text = re.sub(p, '', text)
    # 日本語文字間の不要なスペースを除去（改行由来）
    return re.sub(r'(?<=[　-鿿＀-￯])\s+(?=[　-鿿＀-￯])', '', text)


def split_into_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[。！？])\s*', text)
    return [p.strip() for p in parts if p.strip()]


def build_fields(desc_lines: list[str]) -> dict[str, str]:
    fields = {name: "" for name in CONTENT_FIELDS}

    body = clean_japanese_spaces(" ".join(desc_lines).strip())
    for sentence in split_into_sentences(body):
        category = classify_sentence(sentence)
        if category is None:
            continue
        if fields[category]:
            fields[category] += " " + sentence
        else:
            fields[category] = sentence

    return fields


def has_any_content(fields: dict[str, str]) -> bool:
    return any(fields[name].strip() for name in CONTENT_FIELDS)


def parse_drugs(text: str) -> list[dict]:
    drugs = []
    current_name = None
    current_dosage = None
    current_desc_lines = []
    collecting = False

    def flush():
        nonlocal current_name, current_dosage, current_desc_lines
        if not current_name:
            return

        fields = build_fields(current_desc_lines)
        if has_any_content(fields):
            drugs.append({"医薬品名": current_name, **fields})

        current_name = None
        current_dosage = None
        current_desc_lines = []

    for raw_line in text.splitlines():
        line = raw_line.replace('\x0c', '').strip()
        if is_noise(line) or not line:
            continue

        if is_drug_name(line):
            flush()
            current_name = line
            current_dosage = None
            current_desc_lines = []
            collecting = True
        elif collecting:
            if is_dosage(line) and current_dosage is None:
                current_dosage = line
            else:
                current_desc_lines.append(line)

    flush()
    return drugs


def main():
    if not PDF_FILES:
        print("PDFファイルが見つかりません")
        return

    all_drugs = []
    for pdf_path in PDF_FILES:
        print(f"処理中: {pdf_path.name}")
        text = extract_text_from_pdf(pdf_path)
        drugs = parse_drugs(text)
        print(f"  → {len(drugs)} 件抽出")
        all_drugs.extend(drugs)

    print(f"\n合計: {len(all_drugs)} 件")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
        writer.writeheader()
        writer.writerows(all_drugs)

    print(f"出力完了: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

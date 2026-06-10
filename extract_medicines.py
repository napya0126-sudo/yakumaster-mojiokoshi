#!/usr/bin/env python3
"""
内服薬マスタPDFから医薬品情報を抽出してCSVに出力するスクリプト

各薬品の説明ブロック内の記述を、区切り行の位置で4項目に振り分ける。
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
    r'^[ァ-ヶーｦ-ﾟ]{3,15}$',
]

LINE_NOISE_PATTERNS = [
    r'^＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞$',
]

INLINE_NOISE_PATTERNS = [
    r'＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞',
]

DOSAGE_PATTERN = re.compile(r'^1回\s+')
FIELD_NAMES = ["医薬品名", "効能", "注意事項", "使用方法", "保存方法"]
CONTENT_FIELDS = ["効能", "注意事項", "使用方法", "保存方法"]

# ブロック内の区切り行（行頭がこのパターンなら新しい項目ブロックの開始）
SECTION_STARTERS = [
    ("保存方法", re.compile(
        r'^(?:室温|高温|密閉|直射|湿気|光を避け|気密|冷暗所|冷蔵).{0,30}保存'
        r'|.*保存(?:して|してください|下さい|ください)。?$'
    )),
    ("使用方法", re.compile(
        r'^(?:獣医師の指示|獣医師から|空腹時に|食事や他の薬剤|受医師の指示)'
    )),
    ("注意事項", re.compile(
        r'^(?:本剤投与後|本製品を使用後|本剤に直接|嗜好性が)'
    )),
    ("効能", re.compile(r'^(?:このお薬|この製品|複合)')),
]

# 1行の途中に複数項目が続く場合の分割位置
INLINE_BOUNDARIES = re.compile(
    r'(?=本剤投与後|本製品を使用後|本剤に直接|嗜好性が'
    r'|獣医師の指示|獣医師から|空腹時に与えて|食事や他の薬剤|受医師の指示'
    r'|(?:室温|高温|密閉|直射|湿気|光を避け|気密).{0,30}保存)'
)


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


def clean_line(line: str) -> str:
    text = line.strip()
    for p in INLINE_NOISE_PATTERNS:
        text = re.sub(p, '', text)
    return re.sub(r'(?<=[　-鿿＀-￯])\s+(?=[　-鿿＀-￯])', '', text).strip()


def detect_section_start(line: str) -> str | None:
    for field, pattern in SECTION_STARTERS:
        if pattern.match(line):
            return field
    return None


def split_line_parts(line: str) -> list[str]:
    parts = [p.strip() for p in INLINE_BOUNDARIES.split(line) if p.strip()]
    return parts if parts else [line]


def build_fields(desc_lines: list[str]) -> dict[str, str]:
    fields = {name: "" for name in CONTENT_FIELDS}
    current = "効能"

    for raw_line in desc_lines:
        line = clean_line(raw_line)
        if not line or is_noise(line):
            continue

        for part in split_line_parts(line):
            section = detect_section_start(part)
            if section:
                current = section
            fields[current] = f"{fields[current]} {part}".strip() if fields[current] else part

    return fields


def has_any_content(fields: dict[str, str]) -> bool:
    return any(fields[name].strip() for name in CONTENT_FIELDS)


def parse_drugs(text: str) -> list[dict]:
    drugs = []
    current_name = None
    current_desc_lines = []
    collecting = False

    def flush():
        nonlocal current_name, current_desc_lines
        if not current_name:
            return

        fields = build_fields(current_desc_lines)
        if has_any_content(fields):
            drugs.append({"医薬品名": current_name, **fields})

        current_name = None
        current_desc_lines = []

    for raw_line in text.splitlines():
        line = raw_line.replace('\x0c', '').strip()
        if is_noise(line) or not line:
            continue

        if is_drug_name(line):
            flush()
            current_name = line
            current_desc_lines = []
            collecting = True
        elif collecting:
            if not is_dosage(line):
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

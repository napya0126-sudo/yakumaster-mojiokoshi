#!/usr/bin/env python3
"""
CSV抽出結果の完全性・正確性を検証するスクリプト。

extract_medicines.py と同じ -bbox 抽出ロジックを使い、
1. CSVに混入した誤分類エントリ（偽陽性）を検出
2. 現行パターンが検出できない薬品名形式（偽陰性）を検出
3. 追加の剤形パターンでスキャン
を行い、問題箇所を報告する。
"""

import sys
import re
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from extract_medicines import (
    PDF_FILES, OUTPUT_CSV,
    is_noise, is_dosage, is_drug_name,
    extract_page_lines, pdf_page_count,
    PAGE_FOOTER_START, CONTENT_FIELDS,
)

# ---- 追加する広いパターン（現行スクリプトにない剤形） ----
EXTRA_DRUG_PATTERNS = [
    r'顆粒',
    r'チュアブル|ﾁｭｱﾌﾞﾙ',
    r'ゲル$|ｹﾞﾙ$',
    r'クリーム$|ｸﾘｰﾑ$',
    r'軟膏',
    r'点眼|点耳',
    r'スプレー$|ｽﾌﾟﾚｰ$',
]


def is_extra_drug_name(line: str) -> bool:
    """現行パターン外で薬品名の可能性がある行"""
    if not line or is_dosage(line) or is_noise(line) or is_drug_name(line):
        return False
    return any(re.search(p, line) for p in EXTRA_DRUG_PATTERNS)


# ---- 明らかに内容文であるパターン（薬品名でない） ----
CONTENT_LINE_PATTERNS = [
    r'^このお薬は',
    r'^本剤',
    r'^室温で',
    r'^冷所で',
    r'^冷暗所',
    r'^獣医師の',
    r'^空腹時',
    r'^食後',
    r'^食前',
    r'^投与後',
    r'^与え',
    r'^錠剤を',
    r'^カプセルを',
    r'^服用',
]


def looks_like_content(line: str) -> bool:
    return any(re.search(p, line) for p in CONTENT_LINE_PATTERNS)


def load_csv_rows() -> list[dict]:
    with open(OUTPUT_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_csv_false_positives(rows: list[dict]) -> list[dict]:
    """薬品名列に混入した内容文行を検出"""
    false_positives = []
    for row in rows:
        name = row["医薬品名"]
        if looks_like_content(name):
            false_positives.append(row)
        elif any(name.startswith(kw) for kw in ["このお薬は", "本剤投与"]):
            false_positives.append(row)
    return false_positives


def scan_all_pdfs() -> tuple[list[str], list[str], list[str]]:
    """
    全PDFを -bbox でスキャンし、
    - detected: 現行パターンで検出した薬品名
    - extra: 追加パターンで検出した薬品名候補
    - suspicious: 薬品名でも内容文でもないが薬品名の位置に現れる短い行
    を返す。
    """
    detected: list[str] = []
    extra: list[str] = []
    suspicious: list[str] = []

    for pdf_path in PDF_FILES:
        print(f"スキャン中: {pdf_path.name}", end=" ... ", flush=True)
        try:
            page_count = pdf_page_count(pdf_path)
        except RuntimeError as e:
            print(f"エラー: {e}")
            continue

        in_drug_block = False
        skip_rest_of_page = False

        for page_num in range(1, page_count + 1):
            skip_rest_of_page = False
            for y, text in extract_page_lines(pdf_path, page_num):
                if not text:
                    continue

                if PAGE_FOOTER_START.match(text):
                    skip_rest_of_page = True
                    in_drug_block = False
                    continue

                if skip_rest_of_page or is_noise(text):
                    continue

                if is_drug_name(text):
                    detected.append(text)
                    in_drug_block = True
                    continue

                if is_extra_drug_name(text):
                    extra.append(text)
                    in_drug_block = True
                    continue

                if is_dosage(text):
                    continue

                # 内容文でも薬品名でもない短い行 → 薬品名の可能性
                if (not in_drug_block
                        and not looks_like_content(text)
                        and len(text) < 40
                        and len(text) > 2):
                    suspicious.append(text)

        print(f"完了")

    return detected, extra, suspicious


def main():
    print("=== CSV抽出検証レポート ===\n")

    rows = load_csv_rows()
    csv_names = [r["医薬品名"] for r in rows]
    csv_set = set(csv_names)
    print(f"CSV件数: {len(csv_names)} 件\n")

    # ---- 1. CSV偽陽性チェック ----
    fp = check_csv_false_positives(rows)
    print(f"【A. CSVに混入した非薬品名エントリ（偽陽性）】 {len(fp)} 件")
    if fp:
        for row in fp:
            print(f"  医薬品名: {row['医薬品名'][:70]}")
            for field in CONTENT_FIELDS:
                val = row[field]
                if val:
                    print(f"    {field}: {val[:50]}")
        print()
        print("  ↑ これらは効能・使用方法などの文章が医薬品名欄に入っています。")
        print("    原因: 抽出パターン（錠・液）が文章中の漢字にマッチしている。")
    print()

    # ---- 2. PDFスキャン（-bbox使用）----
    detected, extra, suspicious = scan_all_pdfs()
    print()

    detected_set = set(detected)
    in_pdf_not_csv = [n for n in detected if n not in csv_set]
    in_csv_not_pdf = [n for n in csv_names if n not in detected_set]

    print(f"【B. PDFスキャン結果（現行パターン）】 {len(detected)} 件")
    if in_pdf_not_csv:
        print(f"  うち CSV 未掲載（4項目が空のため正常除外か要確認）: {len(in_pdf_not_csv)} 件")
        for name in in_pdf_not_csv:
            print(f"    - {name}")
    print()

    print(f"【C. CSVにあるがPDFスキャンで未検出】 {len(in_csv_not_pdf)} 件")
    if in_csv_not_pdf:
        # 偽陽性分を除外して表示
        fp_names = {r["医薬品名"] for r in fp}
        real_misses = [n for n in in_csv_not_pdf if n not in fp_names]
        print(f"  うち偽陽性ではないもの（要確認）: {len(real_misses)} 件")
        for name in real_misses[:20]:
            print(f"    - {name}")
        if len(real_misses) > 20:
            print(f"    ... 他 {len(real_misses) - 20} 件")
    print()

    if extra:
        print(f"【D. 追加パターンで検出（現行スクリプトが見逃している可能性）】 {len(extra)} 件")
        for name in extra:
            in_csv = "（CSV掲載済）" if name in csv_set else "★ CSV未掲載"
            print(f"  - {name}  {in_csv}")
        print()
    else:
        print("【D. 追加パターン検出】 0 件（顆粒・チュアブル等の見逃しなし）\n")

    if suspicious:
        print(f"【E. 薬品名の位置に現れる未分類の短い行】 {len(suspicious)} 件")
        print("  （薬品名パターンに未対応の剤形の可能性）")
        for line in suspicious[:30]:
            print(f"  - {line}")
        if len(suspicious) > 30:
            print(f"  ... 他 {len(suspicious) - 30} 件")
        print()

    # ---- サマリー ----
    print("=== サマリー ===")
    print(f"CSV件数（合計）:             {len(csv_names):>4} 件")
    print(f"  うち偽陽性疑い（A）:        {len(fp):>4} 件  ← 修正が必要")
    print(f"PDFスキャン（現行パターン）: {len(detected):>4} 件")
    print(f"  PDF検出・CSV未掲載（B）:   {len(in_pdf_not_csv):>4} 件")
    print(f"追加パターン検出（D）:        {len(extra):>4} 件")
    if extra:
        not_in_csv = sum(1 for n in extra if n not in csv_set)
        print(f"  うちCSV未掲載:             {not_in_csv:>4} 件  ← 抽出漏れの可能性")


if __name__ == "__main__":
    main()

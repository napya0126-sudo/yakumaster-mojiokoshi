#!/usr/bin/env python3
"""
内服薬マスタPDFから医薬品情報を抽出してCSVに出力するスクリプト

PDFでは項目名（効能・注意事項など）が選択不能な文字で印字されている。
抽出できるのは項目名の横の本文のみ。同一項目内は折り返しのみで、
項目の切り替わりは行間の広い改行（Y座標の差）で判定する。

出力列: 医薬品名, 効能, 注意事項, 使用方法, 保存方法
"""

import re
import csv
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

PDF_DIR = Path(__file__).parent

# 同一項目内の折り返し行とみなす Y 座標差（pt）の上限
Y_GAP_MERGE = 13.5


def _pdf_sort_key(p: Path) -> int:
    m = re.search(r'_(\d+)', p.name)
    return int(m.group(1)) if m else 0


PDF_FILES = sorted(PDF_DIR.glob("内服薬_*.pdf"), key=_pdf_sort_key)
OUTPUT_CSV = PDF_DIR / "内服薬_マスタ.csv"

HEADER_PATTERNS = [
    r'^\[10000-10\]$',
    r'^泉南動物病院',
    r'^調剤日：',
    r'^\(\d+ /\d+\)$',
]

FOOTER_PATTERNS = [
    r'^獣医師の指示のもと、用法用量を守ってお使いください',
    r'^別の動物や人体への使用は、絶対におやめください',
    r'^\d{3}-\d{4}',
    r'^TEL',
    r'^http://',
    r'診療時間',
    r'休診日',
    r'^処方担当',
    r'^泉南動物病院',
]

PAGE_FOOTER_START = re.compile(r'^獣医師の指示のもと、用法用量を守ってお使いください')

DRUG_NAME_PATTERNS = [
    r'mg|ｍｇ|㎎|㎍',
    r'ml|ｍl|ｍL|mL',                        # ミリリットル単位の液剤（バイコックス1ml等）
    r'錠(?!剤)',                              # 錠剤（内容文）は除外
    r'カプセル|ｶﾌﾟｾﾙ',
    r'散$',
    r'液(?!を|の|[状中])',                    # 血液・粘液・液の（内容文中の漢字）は除外
    r'シロップ|ｼﾛｯﾌﾟ',                       # 半角ｼﾛｯﾌﾟを追加
    r'細粒|顆粒',
    r'粒\d|粒%',                              # ～粒10%等の粒剤
    r'ﾊﾟｳﾀﾞｰ|パウダー',
    r'エリキシル|ｴﾘｷｼﾙ',                     # エリキシル剤
    r'ゲル|ｹﾞﾙ',                             # ゲル製剤
    r'DS\d',                                  # DSドライシロップ（DS+数字）
    r'%$',                                    # 濃度%で終わる製剤名
    r'\(\d+g\)',                              # (215g)等のグラム表記
    r'^[ァ-ヶーｦ-ﾟ]{2,15}\d+$',            # カタカナ+数字（セファクリア300等）
    r'^[ァ-ヶーｦ-ﾟ]{2,15}[A-Za-zＡ-Ｚ]$', # カタカナ+1文字（ﾌﾟﾛﾍﾊﾟﾌｫｽM等）
    r'^[ｦ-ﾟ]{2,15}-[A-Z]',                  # 半角カタカナ-英字（ｾﾞﾝﾗｰｾﾞ-UDOG等）
    r'^[ァ-ヶｦ-ﾟ]{2,}酸[ァ-ヶｦ-ﾟ]',       # カタカナ+酸+カタカナ（ﾐｺﾌｪﾉｰﾙ酸ﾓﾌｪﾁﾙ等）
    r'^[ァ-ヶーｦ-ﾟ]{3,15}$',
    r'^[一-龿々]{1,4}[ｦ-ﾟ]{2,}',           # 漢字+半角カタカナの化合物名（臭化ｶﾘｳﾑ等）
]

# 内容文の先頭パターン（薬品名でないことが確実な行頭）
CONTENT_LINE_START = re.compile(
    r'^このお薬は|^本剤|^錠剤を|^カプセルを|^この製品は|^このシャンプーは'
)

LINE_NOISE_PATTERNS = [
    r'^＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞$',
]

INLINE_NOISE_PATTERNS = [
    r'＜全\s*\d+\s*(本|錠|包|mL|袋)\s*＞',
]

DOSAGE_PATTERN = re.compile(r'^1回\s*')
FIELD_NAMES = ["医薬品名", "効能", "注意事項", "使用方法", "保存方法"]
CONTENT_FIELDS = ["効能", "注意事項", "使用方法", "保存方法"]


def is_noise(line: str) -> bool:
    return any(re.search(p, line) for p in HEADER_PATTERNS + FOOTER_PATTERNS + LINE_NOISE_PATTERNS)


def is_dosage(line: str) -> bool:
    return bool(DOSAGE_PATTERN.match(line.replace(' ', '')))


def is_drug_name(line: str) -> bool:
    if not line or is_dosage(line) or is_noise(line):
        return False
    if CONTENT_LINE_START.match(line):
        return False
    return any(re.search(p, line) for p in DRUG_NAME_PATTERNS)


def clean_text(text: str) -> str:
    for p in INLINE_NOISE_PATTERNS:
        text = re.sub(p, '', text)
    return re.sub(r'(?<=[　-鿿＀-￯])\s+(?=[　-鿿＀-￯])', '', text).strip()


def pdf_page_count(pdf_path: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    m = re.search(r"Pages:\s+(\d+)", result.stdout)
    if not m:
        raise RuntimeError(f"Could not read page count for {pdf_path}")
    return int(m.group(1))


def extract_page_lines(pdf_path: Path, page_num: int) -> list[tuple[float, str]]:
    result = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page_num), "-l", str(page_num), str(pdf_path), "-"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext -bbox failed for {pdf_path} page {page_num}")

    xml = re.sub(r' xmlns="[^"]+"', '', result.stdout, count=1)
    root = ET.fromstring(xml)

    rows: dict[float, list[tuple[float, str]]] = defaultdict(list)
    for word in root.iter("word"):
        y = round(float(word.get("yMin")), 1)
        x = float(word.get("xMin"))
        text = word.text or ""
        rows[y].append((x, text))

    return [(y, clean_text("".join(t for _, t in sorted(parts)))) for y, parts in sorted(rows.items())]


def merge_content_lines(lines: list[tuple[float, str]]) -> list[str]:
    """Y座標の差で同一項目内の折り返し行をまとめ、項目ブロックのリストを返す。"""
    blocks: list[str] = []
    current_parts: list[str] = []
    last_y: float | None = None

    for y, text in lines:
        if not text:
            continue
        if last_y is not None and (y - last_y) < Y_GAP_MERGE:
            current_parts.append(text)
        else:
            if current_parts:
                blocks.append(clean_text("".join(current_parts)))
            current_parts = [text]
        last_y = y

    if current_parts:
        blocks.append(clean_text("".join(current_parts)))

    return blocks


def blocks_to_fields(blocks: list[str]) -> dict[str, str]:
    fields = {name: "" for name in CONTENT_FIELDS}
    for i, block in enumerate(blocks):
        if i < len(CONTENT_FIELDS):
            fields[CONTENT_FIELDS[i]] = block
    return fields


def parse_pdf(pdf_path: Path) -> list[dict]:
    drugs: list[dict] = []
    current_name: str | None = None
    current_content_lines: list[tuple[float, str]] = []

    def flush():
        nonlocal current_name, current_content_lines
        if not current_name:
            return

        blocks = merge_content_lines(current_content_lines)
        fields = blocks_to_fields(blocks)
        if any(fields[name].strip() for name in CONTENT_FIELDS):
            drugs.append({"医薬品名": current_name, **fields})

        current_name = None
        current_content_lines = []

    page_count = pdf_page_count(pdf_path)
    for page_num in range(1, page_count + 1):
        skip_rest_of_page = False
        for y, text in extract_page_lines(pdf_path, page_num):
            if not text:
                continue

            if PAGE_FOOTER_START.match(text):
                flush()
                current_name = None
                skip_rest_of_page = True
                continue

            if skip_rest_of_page or is_noise(text):
                continue

            if is_drug_name(text):
                flush()
                current_name = text
                current_content_lines = []
                continue

            if current_name is None or is_dosage(text):
                continue

            current_content_lines.append((y, text))

        flush()

    flush()
    return drugs


def main():
    if not PDF_FILES:
        print("PDFファイルが見つかりません")
        return

    all_drugs: list[dict] = []
    for pdf_path in PDF_FILES:
        print(f"処理中: {pdf_path.name}")
        drugs = parse_pdf(pdf_path)
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

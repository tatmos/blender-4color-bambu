#!/usr/bin/env python3
"""
PNG画像を縦2×横3の6等分に分割し、
左上から順に 1〜6 の番号を付けたファイルを出力するスクリプト。
"""

import argparse
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow が必要です: pip install Pillow")
    raise SystemExit(1)


def split_png_6(input_path: str, output_dir: str | None = None, prefix: str | None = None) -> list[Path]:
    """
    PNGを縦2・横3の6等分に分割し、左上から 1〜6 の番号付きで保存する。

    - input_path: 入力PNGのパス
    - output_dir: 出力先ディレクトリ（省略時は入力ファイルと同じディレクトリ）
    - prefix: 出力ファイル名のプレフィックス（省略時は入力ファイル名の拡張子なし）

    戻り値: 保存したファイルパスのリスト
    """
    path = Path(input_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")
    if path.suffix.lower() not in (".png",):
        print("警告: 拡張子が .png ではありません。そのまま処理します。")

    img = Image.open(path).convert("RGBA")
    w, h = img.size

    cols, rows = 3, 2
    cell_w = w // cols
    cell_h = h // rows

    out_dir = Path(output_dir).resolve() if output_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    base_name = prefix if prefix is not None else path.stem

    saved = []
    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col + 1  # 1〜6
            left = col * cell_w
            top = row * cell_h
            box = (left, top, left + cell_w, top + cell_h)
            crop = img.crop(box)
            out_path = out_dir / f"{base_name}_{idx}.png"
            crop.save(out_path, "PNG")
            saved.append(out_path)

    return saved


def main():
    parser = argparse.ArgumentParser(
        description="PNGを縦2×横3の6等分に分割し、左上から1〜6の番号付きで保存する"
    )
    parser.add_argument(
        "input",
        type=str,
        help="入力PNGファイルのパス",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default=None,
        help="出力先ディレクトリ（省略時は入力と同じディレクトリ）",
    )
    parser.add_argument(
        "-p", "--prefix",
        type=str,
        default=None,
        help="出力ファイル名のプレフィックス（省略時は入力ファイル名の拡張子なし）",
    )
    args = parser.parse_args()

    saved = split_png_6(args.input, args.output_dir, args.prefix)
    print(f"6分割して保存しました:")
    for p in saved:
        print(f"  {p}")


if __name__ == "__main__":
    main()

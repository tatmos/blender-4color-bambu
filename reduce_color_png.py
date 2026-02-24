#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
PNGテクスチャを指定色数に減色し、中間色はディザで表現するツール。

- 色数は任意指定（例: 2, 4, 8）。K-means でパレットを算出。
- 減色時に中間色が必要な部分は Floyd–Steinberg ディザで表現。
- アルファはそのまま維持（減色対象は RGB のみ）。
"""

import argparse
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow が必要です: pip install Pillow")
    raise SystemExit(1)


def kmeans_palette(pixels_rgb, k, max_iter=30):
    """
    RGB のピクセルリストから k 色のパレットを K-means で求める。
    pixels_rgb: list of (r,g,b) 各 0..255
    戻り値: list of (r,g,b) 0..255 のパレット
    """
    if not pixels_rgb or len(pixels_rgb) < k:
        # 色が少ない場合は補完
        base = list(pixels_rgb) if pixels_rgb else []
        while len(base) < k:
            v = int(255 * (len(base) + 1) / (k + 1))
            base.append((v, v, v))
        return base[:k]

    # 0..1 で扱う（距離計算を合わせる）
    pts = [(r / 255.0, g / 255.0, b / 255.0) for r, g, b in pixels_rgb]
    step = max(1, len(pts) // k)
    centroids = [pts[i] for i in range(0, len(pts), step)][:k]
    while len(centroids) < k:
        centroids.append((0.0, 0.0, 0.0))

    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    assignments = [0] * len(pts)
    for _ in range(max_iter):
        for i, c in enumerate(pts):
            best_j = 0
            best_d = dist(c, centroids[0])
            for j in range(1, k):
                d = dist(c, centroids[j])
                if d < best_d:
                    best_d, best_j = d, j
            assignments[i] = best_j
        new_c = [[0.0, 0.0, 0.0] for _ in range(k)]
        counts = [0] * k
        for i, c in enumerate(pts):
            j = assignments[i]
            for t in range(3):
                new_c[j][t] += c[t]
            counts[j] += 1
        for j in range(k):
            if counts[j] > 0:
                centroids[j] = tuple(new_c[j][t] / counts[j] for t in range(3))

    return [
        (round(centroids[j][0] * 255), round(centroids[j][1] * 255), round(centroids[j][2] * 255))
        for j in range(k)
    ]


def nearest_palette_index(r, g, b, palette):
    """RGB に最も近いパレットのインデックスを返す。"""
    best_i = 0
    best_d = (r - palette[0][0]) ** 2 + (g - palette[0][1]) ** 2 + (b - palette[0][2]) ** 2
    for i in range(1, len(palette)):
        pr, pg, pb = palette[i]
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def floyd_steinberg_dither(img_rgb, palette, width, height):
    """
    画像（RGB の 2D リスト、値は 0..255）をパレットで Floyd–Steinberg ディザする。
    誤差拡散用に float で作業し、出力ピクセル (R,G,B) の 2D リストを返す。
    """
    f = [[[float(img_rgb[y][x][c]) for c in range(3)] for x in range(width)] for y in range(height)]

    out = [[(0, 0, 0) for _ in range(width)] for _ in range(height)]

    for y in range(height):
        for x in range(width):
            r = max(0, min(255, round(f[y][x][0])))
            g = max(0, min(255, round(f[y][x][1])))
            b = max(0, min(255, round(f[y][x][2])))
            idx = nearest_palette_index(r, g, b, palette)
            pr, pg, pb = palette[idx]
            out[y][x] = (pr, pg, pb)
            # 誤差
            er = f[y][x][0] - pr
            eg = f[y][x][1] - pg
            eb = f[y][x][2] - pb
            # Floyd–Steinberg 係数: 右 7/16, 左下 3/16, 下 5/16, 右下 1/16
            if x + 1 < width:
                f[y][x + 1][0] += er * 7 / 16
                f[y][x + 1][1] += eg * 7 / 16
                f[y][x + 1][2] += eb * 7 / 16
            if y + 1 < height:
                f[y + 1][x][0] += er * 5 / 16
                f[y + 1][x][1] += eg * 5 / 16
                f[y + 1][x][2] += eb * 5 / 16
                if x - 1 >= 0:
                    f[y + 1][x - 1][0] += er * 3 / 16
                    f[y + 1][x - 1][1] += eg * 3 / 16
                    f[y + 1][x - 1][2] += eb * 3 / 16
                if x + 1 < width:
                    f[y + 1][x + 1][0] += er * 1 / 16
                    f[y + 1][x + 1][1] += eg * 1 / 16
                    f[y + 1][x + 1][2] += eb * 1 / 16
    return out


def reduce_color_png(
    input_path: str,
    output_path: str | None = None,
    num_colors: int = 4,
    use_dither: bool = True,
    max_pixels_for_palette: int = 500_000,
    kmeans_iterations: int = 30,
) -> Path:
    """
    PNG を指定色数に減色し、必要に応じてディザをかけて保存する。

    - input_path: 入力 PNG
    - output_path: 出力 PNG（省略時は入力名 + _Ncolor.png）
    - num_colors: 減色後の色数（2 以上）
    - use_dither: True で Floyd–Steinberg ディザを使用
    - max_pixels_for_palette: パレット計算に使う最大ピクセル数（大きい画像用）
    - kmeans_iterations: K-means の反復回数

    戻り値: 保存したファイルの Path
    """
    path = Path(input_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")

    img = Image.open(path).convert("RGBA")
    width, height = img.size
    pixels = list(img.getdata())

    # 不透明部分の RGB だけをパレット計算に使う（透明は黒として扱わず、サンプルから外すこともできる）
    rgb_for_palette = []
    for i, (r, g, b, a) in enumerate(pixels):
        if a < 128:
            continue
        rgb_for_palette.append((r, g, b))
    if not rgb_for_palette:
        rgb_for_palette = [(r, g, b) for r, g, b, a in pixels]

    # ピクセル数が多すぎる場合はサンプリング
    if len(rgb_for_palette) > max_pixels_for_palette:
        step = len(rgb_for_palette) // max_pixels_for_palette
        rgb_for_palette = rgb_for_palette[::step]

    palette = kmeans_palette(rgb_for_palette, num_colors, max_iter=kmeans_iterations)

    # 画像を 2D RGB リストに（アルファは別で保持）
    rgb_2d = [[(pixels[y * width + x][0], pixels[y * width + x][1], pixels[y * width + x][2]) for x in range(width)] for y in range(height)]
    alpha_2d = [[pixels[y * width + x][3] for x in range(width)] for y in range(height)]

    if use_dither:
        out_rgb_2d = floyd_steinberg_dither(rgb_2d, palette, width, height)
    else:
        out_rgb_2d = []
        for y in range(height):
            row = []
            for x in range(width):
                r, g, b = rgb_2d[y][x]
                idx = nearest_palette_index(r, g, b, palette)
                row.append(palette[idx])
            out_rgb_2d.append(row)

    # RGBA に戻して保存
    out_pixels = [
        (out_rgb_2d[y][x][0], out_rgb_2d[y][x][1], out_rgb_2d[y][x][2], alpha_2d[y][x])
        for y in range(height)
        for x in range(width)
    ]
    out_img = Image.new("RGBA", (width, height))
    out_img.putdata(out_pixels)

    out_path = Path(output_path).resolve() if output_path else path.parent / f"{path.stem}_{num_colors}color.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path, "PNG")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="PNGテクスチャを指定色数に減色し、中間色はディザで表現する"
    )
    parser.add_argument("input", type=str, help="入力PNGファイルのパス")
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="出力PNGのパス（省略時は 入力名_Ncolor.png）",
    )
    parser.add_argument(
        "-n", "--colors",
        type=int,
        default=4,
        metavar="N",
        help="減色後の色数（デフォルト: 4）",
    )
    parser.add_argument(
        "--no-dither",
        action="store_true",
        help="ディザを使わず最も近いパレット色にのみ置き換える",
    )
    parser.add_argument(
        "--max-pixels",
        type=int,
        default=500_000,
        metavar="N",
        help="パレット計算に使う最大ピクセル数（デフォルト: 500000）",
    )
    parser.add_argument(
        "--kmeans-iterations",
        type=int,
        default=30,
        metavar="N",
        help="K-means の反復回数（デフォルト: 30）",
    )
    args = parser.parse_args()

    if args.colors < 2:
        parser.error("--colors は 2 以上を指定してください")

    out = reduce_color_png(
        args.input,
        output_path=args.output,
        num_colors=args.colors,
        use_dither=not args.no_dither,
        max_pixels_for_palette=args.max_pixels,
        kmeans_iterations=args.kmeans_iterations,
    )
    print(f"保存しました: {out}")


if __name__ == "__main__":
    main()

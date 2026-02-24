# SPDX-License-Identifier: GPL-3.0-or-later
"""
Blender 上で動く「PNG 減色＋ディザ」ツール。

- スクリプト → ファイルを開く → 本ファイルを選択 → スクリプトを実行
- またはテキストエディタに貼り付けて実行
- 実行後、UV/画像エディタの「画像」メニューに「画像を減色（指定色数＋ディザ）」が追加されます。
  メニューから実行するか、下記の if __name__ で INVOKE_DEFAULT を呼ぶとファイル選択ダイアログが開きます。
  どこからでも F3 で「減色」と検索して実行できます。

Blender 標準の画像 API のみ使用（Pillow 不要）。
"""

import os

import bpy
from bpy_extras.io_utils import ImportHelper


# ---------- 減色コア（CLI 版と同じロジック） ----------

def kmeans_palette(pixels_rgb, k, max_iter=30):
    """RGB のピクセルリストから k 色のパレットを K-means で求める。各 0..255。"""
    if not pixels_rgb or len(pixels_rgb) < k:
        base = list(pixels_rgb) if pixels_rgb else []
        while len(base) < k:
            v = int(255 * (len(base) + 1) / (k + 1))
            base.append((v, v, v))
        return base[:k]

    pts = [(r / 255.0, g / 255.0, b / 255.0) for r, g, b in pixels_rgb]
    step = max(1, len(pts) // k)
    centroids = [list(pts[i]) for i in range(0, len(pts), step)][:k]
    while len(centroids) < k:
        centroids.append([0.0, 0.0, 0.0])

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
                centroids[j] = [new_c[j][t] / counts[j] for t in range(3)]

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
    """画像（RGB 2D、0..255）をパレットで Floyd–Steinberg ディザ。"""
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
            er = f[y][x][0] - pr
            eg = f[y][x][1] - pg
            eb = f[y][x][2] - pb
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


def reduce_color_from_arrays(rgb_2d, alpha_2d, width, height, num_colors, use_dither, max_pixels_for_palette=500000, kmeans_iterations=30):
    """
    rgb_2d[y][x] = (r,g,b) 0..255, alpha_2d[y][x] = 0..255
    返り値: out_rgb_2d (同じ形式), およびパレット（参考用）
    """
    pixels_flat = [
        (rgb_2d[y][x][0], rgb_2d[y][x][1], rgb_2d[y][x][2])
        for y in range(height) for x in range(width)
    ]
    rgb_for_palette = []
    for y in range(height):
        for x in range(width):
            if alpha_2d[y][x] >= 128:
                rgb_for_palette.append(rgb_2d[y][x])
    if not rgb_for_palette:
        rgb_for_palette = list(pixels_flat)
    if len(rgb_for_palette) > max_pixels_for_palette:
        step = len(rgb_for_palette) // max_pixels_for_palette
        rgb_for_palette = rgb_for_palette[::step]

    palette = kmeans_palette(rgb_for_palette, num_colors, max_iter=kmeans_iterations)

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

    return out_rgb_2d, palette


# ---------- Blender オペレーター ----------

class REDUCE_COLOR_OT_png(bpy.types.Operator, ImportHelper):
    """PNG を指定色数に減色し、中間色はディザで表現して保存"""
    bl_idname = "reduce_color.png"
    bl_label = "画像を減色（指定色数＋ディザ）"
    bl_options = {"REGISTER"}

    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})
    num_colors: bpy.props.IntProperty(
        name="色数",
        description="減色後の色数",
        default=4,
        min=2,
        max=32,
        soft_max=16,
    )
    use_dither: bpy.props.BoolProperty(
        name="ディザを使用",
        description="中間色を Floyd–Steinberg ディザで表現する",
        default=True,
    )
    output_filepath: bpy.props.StringProperty(
        name="出力先",
        description="出力 PNG のパス（空なら入力と同じフォルダに 入力名_Ncolor.png）",
        default="",
        subtype="FILE_PATH",
    )
    kmeans_iterations: bpy.props.IntProperty(
        name="K-means 反復回数",
        default=30,
        min=5,
        max=100,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "num_colors")
        layout.prop(self, "use_dither")
        layout.prop(self, "output_filepath")
        layout.prop(self, "kmeans_iterations")

    def execute(self, context):
        input_path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(input_path):
            self.report({"ERROR"}, f"ファイルが見つかりません: {input_path}")
            return {"CANCELLED"}

        # 出力パス（未指定時は入力と同じディレクトリに 名前_Ncolor.png）
        if self.output_filepath.strip():
            output_path = bpy.path.abspath(self.output_filepath.strip())
        else:
            base = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(
                os.path.dirname(input_path),
                base + f"_{self.num_colors}color.png",
            )

        try:
            img = bpy.data.images.load(input_path)
        except Exception as e:
            self.report({"ERROR"}, f"画像の読み込みに失敗しました: {e}")
            return {"CANCELLED"}

        w = img.size[0]
        h = img.size[1]
        # Blender の pixels は float 0..1、RGBA の flat
        raw = list(img.pixels)
        rgb_2d = []
        alpha_2d = []
        for y in range(h):
            row_rgb = []
            row_a = []
            for x in range(w):
                i = (y * w + x) * 4
                r = int(round(raw[i] * 255))
                g = int(round(raw[i + 1] * 255))
                b = int(round(raw[i + 2] * 255))
                a = int(round(raw[i + 3] * 255))
                row_rgb.append((r, g, b))
                row_a.append(a)
            rgb_2d.append(row_rgb)
            alpha_2d.append(row_a)

        # 読み込み済み画像は参照を外してから削除可能（今回だけ使う場合）
        img_name = img.name
        bpy.data.images.remove(img)

        out_rgb_2d, _ = reduce_color_from_arrays(
            rgb_2d, alpha_2d, w, h,
            num_colors=self.num_colors,
            use_dither=self.use_dither,
            kmeans_iterations=self.kmeans_iterations,
        )

        # 出力用 Image を作成して保存
        out_img = bpy.data.images.new(
            "ReduceColorOutput",
            width=w,
            height=h,
            alpha=True,
        )
        out_flat = []
        for y in range(h):
            for x in range(w):
                r, g, b = out_rgb_2d[y][x]
                a = alpha_2d[y][x] / 255.0
                out_flat.extend([r / 255.0, g / 255.0, b / 255.0, a])
        out_img.pixels.foreach_set(out_flat)
        out_img.filepath_raw = output_path
        out_img.file_format = "PNG"
        out_img.save()
        bpy.data.images.remove(out_img)

        self.report({"INFO"}, f"保存しました: {output_path}")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(REDUCE_COLOR_OT_png.bl_idname, text=REDUCE_COLOR_OT_png.bl_label)


def register():
    bpy.utils.register_class(REDUCE_COLOR_OT_png)
    # 画像エディタの「画像」メニューに追加（UV/画像エディタで「画像」→「画像を減色…」）
    try:
        bpy.types.IMAGE_MT_image.append(menu_func)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.IMAGE_MT_image.remove(menu_func)
    except Exception:
        pass
    bpy.utils.unregister_class(REDUCE_COLOR_OT_png)


if __name__ == "__main__":
    register()
    # 実行と同時にダイアログを開く
    bpy.ops.reduce_color.png("INVOKE_DEFAULT")

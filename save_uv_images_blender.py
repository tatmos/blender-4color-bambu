# SPDX-License-Identifier: GPL-3.0-or-later
"""
Blender で UV/マテリアルに使われている画像（JPG・PNG 等）をフォルダにまとめて保存するスクリプト。

- パックされた画像（.blend 内に取り込んだ画像）もファイルとして書き出せます。
- スクリプトを実行後、F3 で「UV画像を保存」と検索して実行するか、
  画像エディタの「画像」メニューから「UV画像をフォルダに保存」を実行してください。
"""

import os
import re

import bpy
from bpy_extras.io_utils import ExportHelper


def images_used_by_selected_objects():
    """選択オブジェクトのマテリアルで参照されている画像のセットを返す。"""
    used = set()
    for obj in bpy.context.selected_objects:
        if obj.type != "MESH" or not obj.data:
            continue
        for slot in getattr(obj.material_slots, "values", []) or []:
            mat = slot.material
            if not mat or not getattr(mat, "node_tree", None):
                continue
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE" or not getattr(node, "image", None):
                    continue
                used.add(node.image)
    return used


def sanitize_filename(name):
    """Blender の画像名をファイル名に使えるようにする。"""
    # 拡張子っぽい suffix は一旦除く（後で format に合わせて付ける）
    base = re.sub(r"\.(png|jpg|jpeg|tga|bmp|exr|hdr)$", "", name, flags=re.I)
    base = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", base)
    return base.strip() or "image"


class SAVE_UV_IMAGES_OT_to_folder(bpy.types.Operator, ExportHelper):
    """UV/マテリアルで使っている画像を指定フォルダに保存する"""
    bl_idname = "save_uv_images.to_folder"
    bl_label = "UV画像をフォルダに保存"
    bl_options = {"REGISTER"}

    # 出力先は「選んだファイルの親フォルダ」を使う（ファイル名は使わない）
    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})

    only_selected: bpy.props.BoolProperty(
        name="選択オブジェクトで使っている画像のみ",
        description="オフにすると bpy.data.images のすべてを保存",
        default=True,
    )
    save_format: bpy.props.EnumProperty(
        name="保存形式",
        description="書き出し形式（元が JPG でも PNG で統一可能）",
        items=(
            ("AUTO", "元の形式", "元のファイル形式のまま（パック画像は PNG）"),
            ("PNG", "PNG", "PNG で保存（アルファあり）"),
            ("JPEG", "JPEG", "JPEG で保存（アルファなし）"),
        ),
        default="AUTO",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "only_selected")
        layout.prop(self, "save_format")
        layout.label(text="※ ダイアログで保存先フォルダを開き、任意のファイル名を指定", icon="INFO")

    def execute(self, context):
        # ダイアログで選んだファイルの親フォルダを出力先にする
        out_dir = bpy.path.abspath(os.path.dirname(self.filepath))
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                self.report({"ERROR"}, f"出力フォルダを作成できません: {e}")
                return {"CANCELLED"}

        if self.only_selected:
            to_save = list(images_used_by_selected_objects())
            if not to_save:
                self.report({"WARNING"}, "選択オブジェクトで参照されている画像がありません")
                return {"CANCELLED"}
        else:
            # パック画像（.blend 内）も含めてすべての画像を保存対象にする
            to_save = [img for img in bpy.data.images if getattr(img, "type", None) == "IMAGE"]

        # 同じベース名が出ないよう、連番を付けることがある
        base_counts = {}
        saved = []
        for img in to_save:
            if img.size[0] == 0 or img.size[1] == 0:
                continue
            base = sanitize_filename(img.name)
            if self.save_format == "AUTO":
                if img.filepath_raw:
                    ext = os.path.splitext(img.filepath_raw)[1].lower() or ".png"
                else:
                    ext = ".png"
            elif self.save_format == "PNG":
                ext = ".png"
            else:
                ext = ".jpg"
            if base not in base_counts:
                base_counts[base] = 0
            base_counts[base] += 1
            stem = base if base_counts[base] == 1 else f"{base}_{base_counts[base]}"
            filepath = os.path.join(out_dir, stem + ext)

            orig_path = img.filepath_raw
            orig_format = getattr(img, "file_format", "PNG")
            try:
                img.filepath_raw = filepath
                if self.save_format == "PNG":
                    img.file_format = "PNG"
                elif self.save_format == "JPEG":
                    img.file_format = "JPEG"
                img.save()
                saved.append(filepath)
            except Exception as e:
                self.report({"ERROR"}, f"保存失敗 {img.name}: {e}")
            finally:
                img.filepath_raw = orig_path
                img.file_format = orig_format

        if saved:
            self.report({"INFO"}, f"{len(saved)} 件を保存しました: {out_dir}")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(SAVE_UV_IMAGES_OT_to_folder.bl_idname, text=SAVE_UV_IMAGES_OT_to_folder.bl_label)


def register():
    bpy.utils.register_class(SAVE_UV_IMAGES_OT_to_folder)
    try:
        bpy.types.IMAGE_MT_image.append(menu_func)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.IMAGE_MT_image.remove(menu_func)
    except Exception:
        pass
    bpy.utils.unregister_class(SAVE_UV_IMAGES_OT_to_folder)


if __name__ == "__main__":
    register()
    bpy.ops.save_uv_images.to_folder("INVOKE_DEFAULT")

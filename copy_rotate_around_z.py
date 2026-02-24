# SPDX-License-Identifier: GPL-3.0-or-later
"""
選択オブジェクトをZ軸で回転したコピーを左・右・下に配置するスクリプト。
間隔はオブジェクトの大きさの約半分。
Blender 4.x / 5.x 用。
"""

import bpy
import math
from mathutils import Vector, Matrix

# 間隔の倍率（オブジェクトサイズに対する比率。1.1 = 今の1.1倍の間隔）
SPACING_RATIO = 1.1


def get_object_size(obj):
    """オブジェクトのバウンディングボックスからサイズ（各軸の長さ）を取得。"""
    try:
        bbox_corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    except Exception:
        bbox_corners = []
    if not bbox_corners:
        return Vector((2.0, 2.0, 2.0))  # フォールバック
    xs = [v.x for v in bbox_corners]
    ys = [v.y for v in bbox_corners]
    zs = [v.z for v in bbox_corners]
    return Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))


def duplicate_and_place_rotated(obj, spacing):
    """
    元オブジェクトを基準に、Z軸回転したコピーを3つ作成して配置する。
    - 左: Z -90°
    - 右: Z +90°
    - 下: Z 180°
    Blenderの座標系: Z上向き。左=-X, 右=+X, 下=-Z。
    """
    scene = bpy.context.scene
    collection = obj.users_collection[0] if obj.users_collection else scene.collection

    base_loc = obj.matrix_world.translation.copy()
    base_rot = obj.matrix_world.to_quaternion()

    # 左: Z -90°、位置は -X
    rot_left = base_rot @ Matrix.Rotation(math.radians(-90), 4, 'Z').to_quaternion()
    loc_left = base_loc + Vector((-spacing, 0, 0))

    # 右: Z +90°、位置は +X
    rot_right = base_rot @ Matrix.Rotation(math.radians(90), 4, 'Z').to_quaternion()
    loc_right = base_loc + Vector((spacing, 0, 0))

    # 下: Z 180°、位置は -Z
    rot_down = base_rot @ Matrix.Rotation(math.radians(180), 4, 'Z').to_quaternion()
    loc_down = base_loc + Vector((0, 0, -spacing))

    copies = []

    for name_suffix, loc, rot in [
        ("_L", loc_left, rot_left),
        ("_R", loc_right, rot_right),
        ("_D", loc_down, rot_down),
    ]:
        dup = obj.copy()
        dup.data = obj.data
        dup.animation_data_clear()
        dup.name = obj.name + name_suffix
        dup.matrix_world = Matrix.LocRotScale(loc, rot, obj.matrix_world.to_scale())
        collection.objects.link(dup)
        copies.append(dup)

    return copies


def main():
    selected = list(bpy.context.selected_objects)
    if not selected:
        print("オブジェクトが選択されていません。")
        return

    # オブジェクトモードで実行（編集モードだと選択がメッシュになるため）
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    created = []
    for obj in selected:
        size = get_object_size(obj)
        # 最大辺の半分を間隔として使用
        spacing = max(size.x, size.y, size.z) * SPACING_RATIO
        copies = duplicate_and_place_rotated(obj, spacing)
        created.extend(copies)
        print(f"{obj.name}: 間隔={spacing:.3f}, 左・右・下にコピーを配置しました。")

    if created:
        bpy.ops.object.select_all(action='DESELECT')
        for c in created:
            c.select_set(True)
        bpy.context.view_layer.objects.active = created[0]
        print(f"合計 {len(created)} 個のコピーを作成しました。")


if __name__ == "__main__":
    main()

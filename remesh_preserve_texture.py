# SPDX-License-Identifier: GPL-3.0-or-later
# Blender 5.0 向け: リメッシュしつつ、リメッシュ前のテクスチャをベイクで転写するスクリプト
#
# 使い方: テクスチャ付きメッシュを選択 → スクリプト実行
# 結果: 元オブジェクトのコピーがリメッシュされ、元の見た目がベイクされたテクスチャで適用されたオブジェクトが追加される

import bpy

# ---------- リメッシュ設定（好みで変更） ----------
REMESH_MODE = "BLOCKS"  # "BLOCKS", "SMOOTH", "SHARP", "VOXEL"
OCTREE_DEPTH = 8       # BLOCKS 時の解像度（大きいほど細かい）
REMESH_SCALE = 0.9     # BLOCKS 時のスケール
VOXEL_SIZE = 0.1       # VOXEL モード時のボクセルサイズ
REMOVE_DISCONNECTED = True
REMESH_THRESHOLD = 1.0

# ---------- ベイク設定 ----------
BAKE_IMAGE_SIZE = 1024  # ベイク画像の解像度（幅・高さ）
BAKE_MARGIN = 16
# 照明なしで表面色のみベイク（テクスチャの色をそのまま転写）
BAKE_PASS_DIRECT = False
BAKE_PASS_INDIRECT = False
BAKE_PASS_EMIT = True
BAKE_PASS_GLOSSY = False
BAKE_PASS_TRANSMISSION = False
BAKE_PASS_COLOR = True
BAKE_PASS_DIFFUSE = True

# 元オブジェクトをベイク後に非表示にする
HIDE_ORIGINAL_AFTER_BAKE = True


def get_remesh_modifier(obj):
    for m in obj.modifiers:
        if m.type == "REMESH":
            return m
    return None


def ensure_remesh_modifier(obj, mode="BLOCKS", octree_depth=8, scale=0.9,
                           voxel_size=0.1, remove_disconnected=True, threshold=1.0):
    """リメッシュモディファイアを追加または取得し、パラメータを設定する。"""
    mod = get_remesh_modifier(obj)
    if mod is None:
        bpy.ops.object.modifier_add(type="REMESH")
        mod = get_remesh_modifier(obj)
    if mod is None:
        return None
    mod.mode = mode
    if mode == "VOXEL":
        mod.voxel_size = voxel_size
    else:
        if hasattr(mod, "octree_depth"):
            mod.octree_depth = octree_depth
        if hasattr(mod, "scale"):
            mod.scale = scale
    if hasattr(mod, "use_remove_disconnected"):
        mod.use_remove_disconnected = remove_disconnected
    if hasattr(mod, "threshold"):
        mod.threshold = threshold
    return mod


def apply_remesh(obj, mode="BLOCKS", octree_depth=8, scale=0.9, voxel_size=0.1,
                 remove_disconnected=True, threshold=1.0):
    """オブジェクトにリメッシュを適用する。"""
    mod = ensure_remesh_modifier(
        obj, mode=mode, octree_depth=octree_depth, scale=scale,
        voxel_size=voxel_size, remove_disconnected=remove_disconnected, threshold=threshold
    )
    if mod is None:
        return False
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
        return True
    except Exception as e:
        print(f"リメッシュ適用エラー: {e}")
        return False


def smart_uv_remesh_object(obj, angle_limit=66.0, island_margin=0.0):
    """オブジェクトを編集モードで選択し、Smart UV Project を実行する。"""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=angle_limit, island_margin=island_margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    return True


def ensure_bake_target_material(obj, image_size=1024):
    """
    リメッシュ後のオブジェクトに、ベイク先の画像を持つマテリアルを用意する。
    既存マテリアルを1つ使い、その中に Image Texture ノード（新規画像）を追加して
    アクティブにし、ベイク結果が表示されるよう Base Color に接続する。
    """
    mesh = obj.data
    if not mesh.materials:
        mat = bpy.data.materials.new(name=obj.name + "_Baked")
        mat.use_nodes = True
        mesh.materials.append(mat)
    else:
        mat = mesh.materials[0]
        if mat is None:
            mat = bpy.data.materials.new(name=obj.name + "_Baked")
            mat.use_nodes = True
            mesh.materials[0] = mat
        elif not mat.use_nodes:
            mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # 既存の Image Texture で「Baked」を含むものがあれば流用
    img = None
    tex_node = None
    for n in nodes:
        if n.type == "TEX_IMAGE" and n.image and "Baked" in (n.image.name or ""):
            tex_node = n
            img = n.image
            break
    if img is None:
        img = bpy.data.images.new(
            name=obj.name + "_BakedTexture",
            width=image_size,
            height=image_size,
            alpha=True,
        )
    if tex_node is None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = img

    tex_node.image = img
    nodes.active = tex_node

    # Principled BSDF を探して Base Color に接続
    principled = None
    for n in nodes:
        if n.type == "BSDF_PRINCIPLED":
            principled = n
            break
    if principled is not None:
        # 既存の Base Color 接続を外して、ベイク画像に差し替え
        for link in list(links):
            if link.to_node == principled and link.to_socket.name == "Base Color":
                links.remove(link)
        links.new(tex_node.outputs["Color"], principled.inputs["Base Color"])

    return True


def bake_selected_to_active(source_obj, target_obj, image_size=1024, margin=16):
    """
    選択オブジェクト（source）の見た目を、アクティブオブジェクト（target）の
    UV にベイクする。target にはあらかじめ UV とベイク先マテリアルを用意しておく。
    """
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    scene.render.engine = "CYCLES"
    if hasattr(scene, "cycles"):
        scene.cycles.bake_type = "DIFFUSE"

    bake = getattr(scene.render, "bake", None)
    if bake is None:
        print("BakeSettings が見つかりません（Blender バージョン確認）")
        return False

    bake.use_selected_to_active = True
    bake.target = "IMAGE_TEXTURES"
    bake.width = image_size
    bake.height = image_size
    bake.margin = margin
    bake.use_clear = True

    if hasattr(bake, "use_pass_direct"):
        bake.use_pass_direct = BAKE_PASS_DIRECT
    if hasattr(bake, "use_pass_indirect"):
        bake.use_pass_indirect = BAKE_PASS_INDIRECT
    if hasattr(bake, "use_pass_emit"):
        bake.use_pass_emit = BAKE_PASS_EMIT
    if hasattr(bake, "use_pass_glossy"):
        bake.use_pass_glossy = BAKE_PASS_GLOSSY
    if hasattr(bake, "use_pass_transmission"):
        bake.use_pass_transmission = BAKE_PASS_TRANSMISSION
    if hasattr(bake, "use_pass_color"):
        bake.use_pass_color = BAKE_PASS_COLOR
    if hasattr(bake, "use_pass_diffuse"):
        bake.use_pass_diffuse = BAKE_PASS_DIFFUSE

    bpy.ops.object.select_all(action="DESELECT")
    source_obj.select_set(True)
    target_obj.select_set(True)
    view_layer.objects.active = target_obj

    try:
        bpy.ops.object.bake(type="DIFFUSE")
        return True
    except Exception as e:
        print(f"ベイク失敗: {e}")
        return False


def remesh_preserve_texture(
    remesh_mode=None,
    octree_depth=None,
    scale=None,
    voxel_size=None,
    remove_disconnected=None,
    threshold=None,
    image_size=None,
    hide_original=None,
):
    """
    アクティブオブジェクトを複製し、複製にリメッシュを適用したうえで、
    元オブジェクトのテクスチャを「選択→アクティブ」ベイクで転写する。
    """
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        print("メッシュオブジェクトを選択してから実行してください。")
        return None

    mode = remesh_mode if remesh_mode is not None else REMESH_MODE
    octree = octree_depth if octree_depth is not None else OCTREE_DEPTH
    rem_scale = scale if scale is not None else REMESH_SCALE
    voxel = voxel_size if voxel_size is not None else VOXEL_SIZE
    remove_disc = remove_disconnected if remove_disconnected is not None else REMOVE_DISCONNECTED
    thresh = threshold if threshold is not None else REMESH_THRESHOLD
    img_size = image_size if image_size is not None else BAKE_IMAGE_SIZE
    hide_orig = hide_original if hide_original is not None else HIDE_ORIGINAL_AFTER_BAKE

    # 1) 複製（元 = テクスチャ源、複製 = リメッシュ対象）
    bpy.ops.object.duplicate(linked=False)
    remesh_obj = bpy.context.active_object
    remesh_obj.name = obj.name + "_remeshed"

    # 2) 複製にリメッシュ適用
    if not apply_remesh(
        remesh_obj,
        mode=mode,
        octree_depth=octree,
        scale=rem_scale,
        voxel_size=voxel,
        remove_disconnected=remove_disc,
        threshold=thresh,
    ):
        print("リメッシュの適用に失敗しました。")
        bpy.data.objects.remove(remesh_obj, do_unlink=True)
        return None

    # 3) リメッシュ後メッシュに Smart UV
    smart_uv_remesh_object(remesh_obj)

    # 4) ベイク先マテリアル・画像ノードを用意
    ensure_bake_target_material(remesh_obj, image_size=img_size)

    # 5) 選択→アクティブでベイク（元を選択、リメッシュ後をアクティブ）
    ok = bake_selected_to_active(
        obj, remesh_obj, image_size=img_size, margin=BAKE_MARGIN
    )
    if not ok:
        print("ベイクに失敗しました。リメッシュ済みオブジェクトは残しています。")

    if hide_orig:
        obj.hide_set(True)

    print("完了: リメッシュ＋テクスチャ転写オブジェクト =", remesh_obj.name)
    return remesh_obj


# スクリプト単体実行時
if __name__ == "__main__":
    remesh_preserve_texture()

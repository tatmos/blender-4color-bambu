# SPDX-License-Identifier: GPL-3.0-or-later
# Blender 5.0.1 向け: 4色減色 → オブジェクト分割 → 3MFエクスポート（Bambu Studio 2.5.0.66 等）

import bpy
import bmesh

# ---------- 設定 ----------
OUTPUT_PATH = "D:/3DCG/output_4colors_quantized_only.3mf"
NUM_COLORS = 4
KMEANS_ITERATIONS = 20


def get_face_colors_from_mesh(obj):
    """メッシュから面ごとの色を取得。頂点色またはマテリアルから取得。"""
    mesh = obj.data
    if not mesh.polygons:
        return None, []

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    # 頂点色レイヤーを探す（Blender 4.0+ は "Color" や col 属性）
    color_layer = None
    if bm.loops.layers.color:
        color_layer = bm.loops.layers.color.active
        if color_layer is None:
            color_layer = bm.loops.layers.color.get("Color") or next(iter(bm.loops.layers.color), None)

    face_colors = []
    has_vertex_color = color_layer is not None

    if has_vertex_color:
        for face in bm.faces:
            r, g, b = 0.0, 0.0, 0.0
            n = len(face.loops)
            for loop in face.loops:
                c = loop[color_layer]
                r += c[0]
                g += c[1]
                b += c[2]
            face_colors.append((r / n, g / n, b / n))
    else:
        # マテリアルベース: 面のマテリアルインデックスからベースカラー取得
        for face in bm.faces:
            mat_index = face.material_index
            if mat_index is not None and mat_index < len(obj.material_slots):
                mat = obj.material_slots[mat_index].material
                if mat and mat.use_nodes:
                    base_color = (0.5, 0.5, 0.5)
                    for n in mat.node_tree.nodes:
                        if n.type == "BSDF_PRINCIPLED":
                            base_color = n.inputs["Base Color"].default_value[:3]
                            break
                    face_colors.append(tuple(base_color))
                else:
                    face_colors.append((0.5, 0.5, 0.5))
            else:
                face_colors.append((0.5, 0.5, 0.5))

    bm.free()
    return has_vertex_color, face_colors


def quantize_colors_kmeans(face_colors, k=4, max_iter=20):
    """面の色リストを k 色に減色（簡易 k-means）。"""
    if not face_colors or len(face_colors) < k:
        # 色が少ない場合はそのまま or 補完
        while len(face_colors) < k:
            face_colors = face_colors + [(0.2, 0.2, 0.2), (0.5, 0.5, 0.5), (0.8, 0.8, 0.8)][:k - len(face_colors)]
        return list(face_colors)[:k], [0] * len(face_colors)

    # 初期重心: なるべく離すためにサンプルから選ぶ
    step = max(1, len(face_colors) // k)
    centroids = [tuple(face_colors[i]) for i in range(0, len(face_colors), step)][:k]
    while len(centroids) < k:
        centroids.append((0.0, 0.0, 0.0))

    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    assignments = [0] * len(face_colors)
    for _ in range(max_iter):
        # assign
        for i, c in enumerate(face_colors):
            best = 0
            best_d = dist(c, centroids[0])
            for j in range(1, k):
                d = dist(c, centroids[j])
                if d < best_d:
                    best_d = d
                    best = j
            assignments[i] = best
        # update centroids
        new_centroids = [[0.0, 0.0, 0.0] for _ in range(k)]
        counts = [0] * k
        for i, c in enumerate(face_colors):
            j = assignments[i]
            for t in range(3):
                new_centroids[j][t] += c[t]
            counts[j] += 1
        for j in range(k):
            if counts[j] > 0:
                centroids[j] = tuple(new_centroids[j][t] / counts[j] for t in range(3))

    return [tuple(c) for c in centroids], assignments


def mesh_split_by_color(obj, face_colors, assignments, palette):
    """メッシュを色ごとに分割し、各色で新しいオブジェクトを作成。"""
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    # 色インデックスごとに面をグループ化（頂点はグローバルインデックスで管理）
    from collections import defaultdict
    groups = defaultdict(list)  # color_index -> list of (verts_indices, face_as_vertex_indices)
    for face_idx, face in enumerate(bm.faces):
        color_idx = assignments[face_idx] if face_idx < len(assignments) else 0
        vert_indices = [v.index for v in face.verts]
        groups[color_idx].append(vert_indices)

    bm.free()

    # 各グループごとにメッシュを構築
    new_objects = []
    for color_idx, face_vert_lists in groups.items():
        if not face_vert_lists:
            continue

        # このグループで使う頂点のユニーク集合と、旧インデックス→新インデックス
        all_verts = set()
        for fv in face_vert_lists:
            all_verts.update(fv)
        old_to_new = {old: new for new, old in enumerate(sorted(all_verts))}

        verts_global = list(mesh.vertices)
        new_verts = [verts_global[i].co.copy() for i in sorted(all_verts)]
        new_faces = [tuple(old_to_new[v] for v in fv) for fv in face_vert_lists]

        new_mesh = bpy.data.meshes.new(name=f"{obj.name}_color{color_idx}")
        new_mesh.from_pydata(new_verts, [], new_faces)
        new_mesh.update()

        new_obj = bpy.data.objects.new(name=f"{obj.name}_color{color_idx}", object_data=new_mesh)
        new_obj.matrix_world = obj.matrix_world.copy()
        bpy.context.collection.objects.link(new_obj)

        # マテリアルを1色で設定（3MF/Bambuで色として認識されやすくする）
        color = palette[color_idx]
        mat_name = f"Color_{color_idx}_{obj.name}"
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(name=mat_name)
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            principled = None
            for n in nodes:
                if n.type == "BSDF_PRINCIPLED":
                    principled = n
                    break
            if principled:
                principled.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        new_obj.data.materials.append(mat)
        new_objects.append(new_obj)

    return new_objects


def process_scene():
    """選択メッシュを4色減色・分割し、3MFエクスポートする。"""
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    # 対象: 選択中のメッシュオブジェクト（未選択なら全メッシュ）
    if bpy.context.selected_objects:
        candidates = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    else:
        candidates = [o for o in scene.objects if o.type == "MESH"]

    if not candidates:
        print("メッシュオブジェクトがありません。")
        return

    # 既存の「分割済み」オブジェクトを削除するかはオプション。ここでは新規作成のみ。
    created = []
    for obj in candidates:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        view_layer.objects.active = obj

        has_vcol, face_colors = get_face_colors_from_mesh(obj)
        if not face_colors:
            continue

        palette, assignments = quantize_colors_kmeans(face_colors, k=NUM_COLORS, max_iter=KMEANS_ITERATIONS)
        new_objs = mesh_split_by_color(obj, face_colors, assignments, palette)
        created.extend(new_objs)

    if not created:
        print("分割できるメッシュがありません。")
        return

    # エクスポート用に分割オブジェクトのみ選択
    bpy.ops.object.select_all(action="DESELECT")
    for o in created:
        o.select_set(True)

    # 3MF エクスポート（アドオンで export_mesh.threemf が登録されている前提）
    try:
        bpy.ops.export_mesh.threemf(filepath=OUTPUT_PATH)
        print(f"減色された3MFファイルをエクスポートしました: {OUTPUT_PATH}")
    except AttributeError:
        # アドオンが three_mf などの別名で登録している場合
        try:
            bpy.ops.export_mesh.three_mf(filepath=OUTPUT_PATH)
            print(f"減色された3MFファイルをエクスポートしました: {OUTPUT_PATH}")
        except AttributeError:
            print("3MFエクスポートが見つかりません。Blenderに「3MF format」アドオンを有効にしてください。")
            print("エクスポートパス:", OUTPUT_PATH)


if __name__ == "__main__":
    process_scene()

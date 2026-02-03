# SPDX-License-Identifier: GPL-3.0-or-later
# Blender 5.0.1 向け: 4色減色 → オブジェクト分割 → 3MFエクスポート（Bambu Studio 2.5.0.66 等）

import bpy
import bmesh

# ---------- 設定 ----------
OUTPUT_PATH = "D:/3DCG/output_4colors_quantized_only.3mf"
NUM_COLORS = 4
KMEANS_ITERATIONS = 20
EXPORT_SCALE = 0.1  # 出力サイズを10%に
USE_SELECTION_ONLY = True  # True: 選択されたメッシュのみ処理（1体だけ出力したい場合は1つだけ選択）


def get_face_colors_from_mesh(obj):
    """メッシュから面ごとの色を取得。頂点色またはマテリアルから取得。"""
    mesh = obj.data
    if not mesh.polygons:
        return None, []

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    # 頂点色レイヤーを探す（"Color" → "Col"（Tripo/OBJ等）→ active → 最初のレイヤー）
    color_layer = None
    layer_name_used = None
    use_float_layer = False

    def pick_byte_color_layer():
        nonlocal color_layer, layer_name_used
        if not getattr(bm.loops.layers, "color", None) or not bm.loops.layers.color:
            return
        layers = bm.loops.layers.color
        for preferred in ("Color", "Col"):
            color_layer = layers.get(preferred)
            if color_layer is not None:
                layer_name_used = preferred
                return
        if getattr(layers, "active", None) is not None:
            color_layer = layers.active
            layer_name_used = "active"
            return
        for name, layer in layers.items():
            color_layer = layer
            layer_name_used = name
            return

    def pick_float_color_layer():
        nonlocal color_layer, layer_name_used, use_float_layer
        if not getattr(bm.loops.layers, "float_color", None) or not bm.loops.layers.float_color:
            return
        layers = bm.loops.layers.float_color
        for preferred in ("Color", "Col"):
            layer = layers.get(preferred)
            if layer is not None:
                layer_name_used = preferred
                break
        else:
            layer = None
        if layer is None and getattr(layers, "active", None) is not None:
            layer = layers.active
            layer_name_used = "active"
        elif layer is None:
            for name, l in layers.items():
                layer = l
                layer_name_used = name
                break
        if layer is not None:
            color_layer = layer
            use_float_layer = True

    pick_byte_color_layer()
    if color_layer is None:
        pick_float_color_layer()
    if layer_name_used:
        print(f"  頂点色レイヤーを使用: \"{layer_name_used}\" ({'float' if use_float_layer else 'byte'}) (オブジェクト: {obj.name})")

    face_colors = []
    has_vertex_color = color_layer is not None

    if has_vertex_color:
        for face in bm.faces:
            r, g, b = 0.0, 0.0, 0.0
            n = len(face.loops)
            for loop in face.loops:
                c = loop[color_layer]
                # byte は 0-1 で渡ってくる想定。float も 0-1
                r += float(c[0])
                g += float(c[1])
                b += float(c[2])
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


def ensure_distinct_palette(palette, k=4):
    """パレットがほぼ同じ色（例: 全部白）なら、視覚的に区別しやすい4色に置き換える。"""
    if len(palette) < k:
        palette = list(palette) + [(0.2, 0.2, 0.2)] * (k - len(palette))
    # パレット内の明るさ・ばらつきを簡易チェック
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in palette]
    avg_lum = sum(lum) / len(lum)
    variance = sum((x - avg_lum) ** 2 for x in lum) / max(len(lum), 1)
    # ほぼ同じ明るさで分散が小さい → 区別しやすい4色に（白っぽい or 黒っぽい）
    if variance < 0.02 and avg_lum > 0.8:
        return [(0.9, 0.2, 0.2), (0.2, 0.5, 0.9), (0.2, 0.75, 0.3), (0.9, 0.85, 0.2)]  # 赤・青・緑・黄
    if variance < 0.02 and avg_lum < 0.25:
        return [(0.9, 0.25, 0.25), (0.25, 0.5, 0.9), (0.25, 0.8, 0.35), (0.95, 0.9, 0.25)]
    return list(palette)[:k]


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
        # 全ポリゴンにマテリアル0を割り当て（後でマテリアルを1つ追加する）
        for poly in new_mesh.polygons:
            poly.material_index = 0
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

    # 対象: USE_SELECTION_ONLY のときは選択メッシュのみ、そうでなければ全メッシュ
    if USE_SELECTION_ONLY:
        candidates = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not candidates:
            print("メッシュオブジェクトを1つ以上選択してから実行してください。（1体だけ出力する場合は1つだけ選択）")
            return
        print(f"[調査] 処理対象: 選択されたメッシュ {len(candidates)} 個")
    else:
        if bpy.context.selected_objects:
            candidates = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        else:
            candidates = [o for o in scene.objects if o.type == "MESH"]
        print(f"[調査] 処理対象: メッシュ {len(candidates)} 個")
    if not candidates:
        print("メッシュオブジェクトがありません。")
        return
    for i, o in enumerate(candidates):
        print(f"  - [{i}] {o.name}")

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
        # 頂点色がほぼ一色だと全面が同じクラスタ(0)になる → 面を均等に4分割する
        if len(set(assignments)) < 2:
            assignments = [i % NUM_COLORS for i in range(len(assignments))]
            print("  頂点色が一色のため、面を均等に4分割しました。")
        palette = ensure_distinct_palette(palette, k=NUM_COLORS)
        new_objs = mesh_split_by_color(obj, face_colors, assignments, palette)
        created.extend(new_objs)

    if not created:
        print("分割できるメッシュがありません。")
        return

    print(f"[調査] 作成した分割オブジェクト: {len(created)} 個（色ごと）")
    for i, o in enumerate(created):
        mat_info = ""
        if o.data.materials:
            mat = o.data.materials[0]
            if mat and mat.node_tree and mat.node_tree.nodes:
                for n in mat.node_tree.nodes:
                    if n.type == "BSDF_PRINCIPLED":
                        col = n.inputs["Base Color"].default_value
                        mat_info = f" RGB({col[0]:.2f},{col[1]:.2f},{col[2]:.2f})"
                        break
        print(f"  - [{i}] {o.name}{mat_info}")

    # 元オブジェクトをシーンから一時的に外す（コレクションから unlink → 3MF に絶対含まれないようにする）
    restored_collections = []  # (obj, [col, col, ...])
    for obj in candidates:
        cols = list(obj.users_collection)
        restored_collections.append((obj, cols))
        for c in cols:
            c.objects.unlink(obj)
    print("[調査] 元オブジェクトをコレクションから外しました（エクスポート後に復元します）")

    try:
        # エクスポート用に分割オブジェクトのみ選択
        bpy.ops.object.select_all(action="DESELECT")
        for o in created:
            o.select_set(True)
        view_layer.objects.active = created[0]

        # まず各オブジェクトのトランスフォームをメッシュに焼き込む（ワールド空間に統一）
        # これで元オブジェクトごとのスケール差がなくなり、全オブジェクトが同じ基準になる
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # そのうえで出力スケール（10%）を適用
        if EXPORT_SCALE != 1.0:
            for o in created:
                o.scale *= EXPORT_SCALE
            bpy.ops.object.transform_apply(scale=True)

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
    finally:
        # 元オブジェクトをコレクションに戻す
        for obj, cols in restored_collections:
            for c in cols:
                c.objects.link(obj)
        print("[調査] 元オブジェクトをコレクションに戻しました。")


if __name__ == "__main__":
    process_scene()

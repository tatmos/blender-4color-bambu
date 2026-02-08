# SPDX-License-Identifier: GPL-3.0-or-later
# Blender 5.0.1 向け: 4色減色 → オブジェクト分割 → 3MFエクスポート（Bambu Studio 2.5.0.66 等）

import bpy
import bmesh
from bpy_extras.io_utils import ExportHelper

# ---------- 設定 ----------
OUTPUT_PATH = "D:/3DCG/output_4colors_quantized_only.3mf"
# エクスポート形式: "3mf" = 3MF（Bambu等）, "obj" = 頂点色付きOBJ（Bambu等で色が取れる場合あり）
EXPORT_FORMAT = "obj"  # "3mf" または "obj"
NUM_COLORS = 4
KMEANS_ITERATIONS = 20
EXPORT_SCALE = 0.1  # 出力サイズを10%に
USE_SELECTION_ONLY = True  # True: 選択されたメッシュのみ処理（1体だけ出力したい場合は1つだけ選択）
BAKE_TO_VERTEX_COLOR = True  # True: 表示色を頂点カラーにベイクしてから減色（テクスチャ等も反映）
BAKE_TARGET_ATTR_NAME = "Col"  # ベイク先のカラー属性名（"Col" で既存を上書き / "Color" で新規）

# エクスポートモード: "split" = 色ごとにメッシュ分割（従来）, "vertex_color_only" = 分割せず頂点色のみ（非多様体回避）
EXPORT_MODE = "vertex_color_only"  # 非多様体エッジを避けたい場合はこのまま。従来どおり分割したい場合は "split"

# 進捗ログ: この件数ごとにコンソールに出力（0 で無効）
PROGRESS_LOG_INTERVAL = 5000


def ensure_bake_target_color_attribute(mesh, name):
    """メッシュにカラー属性がなければ追加。ドメインは FACE_CORNER（ループ）。"""
    idx = mesh.color_attributes.find(name)
    if idx >= 0:
        return mesh.color_attributes[idx]
    return mesh.color_attributes.new(name=name, type="BYTE_COLOR", domain="CORNER")


def bake_material_to_vertex_colors(obj, target_attr_name="Col"):
    """
    オブジェクトのマテリアル表示色（テクスチャ含む）を頂点カラーにベイクする。
    Cycles のベイクで target=VERTEX_COLORS を使用。ベイク先はアクティブなカラー属性。
    """
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    mesh = obj.data
    if not mesh.polygons:
        return False
    # ベイク先のカラー属性を用意（既存があればそれを使い、なければ新規）
    attr = ensure_bake_target_color_attribute(mesh, target_attr_name)
    mesh.color_attributes.active_color_index = mesh.color_attributes.find(attr.name)
    # レンダーエンジンを Cycles に
    scene.render.engine = "CYCLES"
    if hasattr(scene, "cycles"):
        scene.cycles.bake_type = "DIFFUSE"
        if hasattr(scene.cycles, "bake_direct"):
            scene.cycles.bake_direct = False
        if hasattr(scene.cycles, "bake_indirect"):
            scene.cycles.bake_indirect = False
    # BakeSettings (Blender 4.x/5.x): 頂点カラーへベイク
    bake = getattr(scene.render, "bake", None)
    if bake is not None and hasattr(bake, "target"):
        bake.target = "VERTEX_COLORS"
    # 選択をこのオブジェクトだけに
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    view_layer.objects.active = obj
    try:
        bpy.ops.object.bake(type="DIFFUSE")
        return True
    except Exception as e:
        print(f"  ベイク失敗: {e}")
        return False


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
    n_faces = len(bm.faces)
    log_interval = PROGRESS_LOG_INTERVAL if PROGRESS_LOG_INTERVAL > 0 else n_faces + 1

    if has_vertex_color:
        for fi, face in enumerate(bm.faces):
            if (fi + 1) % log_interval == 0 or fi == 0 or fi == n_faces - 1:
                print(f"    面の色取得: {fi + 1}/{n_faces}")
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
        for fi, face in enumerate(bm.faces):
            if (fi + 1) % log_interval == 0 or fi == 0 or fi == n_faces - 1:
                print(f"    面の色取得: {fi + 1}/{n_faces}")
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

    n_fc = len(face_colors)
    if PROGRESS_LOG_INTERVAL > 0 and n_fc >= PROGRESS_LOG_INTERVAL:
        print(f"  K-means 減色中: {n_fc} 面, {max_iter} 反復")

    assignments = [0] * len(face_colors)
    for it in range(max_iter):
        if PROGRESS_LOG_INTERVAL > 0 and n_fc >= PROGRESS_LOG_INTERVAL and (it + 1) % max(1, max_iter // 4) == 0:
            print(f"    反復 {it + 1}/{max_iter}")
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
    n_faces = len(bm.faces)
    log_interval = PROGRESS_LOG_INTERVAL if PROGRESS_LOG_INTERVAL > 0 else n_faces + 1
    if log_interval <= n_faces:
        print(f"  メッシュ分割: {n_faces} 面を色ごとにグループ化中")
    for face_idx, face in enumerate(bm.faces):
        if (face_idx + 1) % log_interval == 0 or face_idx == 0 or face_idx == n_faces - 1:
            print(f"    面グループ化: {face_idx + 1}/{n_faces}")
        color_idx = assignments[face_idx] if face_idx < len(assignments) else 0
        vert_indices = [v.index for v in face.verts]
        groups[color_idx].append(vert_indices)

    bm.free()

    # 各グループごとにメッシュを構築
    new_objects = []
    for color_idx, face_vert_lists in groups.items():
        if not face_vert_lists:
            continue
        if PROGRESS_LOG_INTERVAL > 0:
            print(f"    色 {color_idx}: {len(face_vert_lists)} 面のメッシュ作成中")

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


def apply_quantized_vertex_colors(obj, face_colors, assignments, palette, attr_name=None):
    """
    メッシュを分割せず、減色した色を頂点カラー（面コーナー）に書き戻す。
    1メッシュのままなので非多様体エッジは発生しない。
    """
    mesh = obj.data
    if not mesh.polygons or not mesh.loops:
        return False
    attr_name = attr_name or BAKE_TARGET_ATTR_NAME
    # カラー属性を用意（byte, CORNER）
    idx = mesh.color_attributes.find(attr_name)
    if idx < 0:
        mesh.color_attributes.new(name=attr_name, type="BYTE_COLOR", domain="CORNER")
        idx = mesh.color_attributes.find(attr_name)
    if idx < 0:
        return False
    mesh.color_attributes.active_color_index = idx

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    color_layer = bm.loops.layers.color.get(attr_name)
    if color_layer is None:
        color_layer = bm.loops.layers.color.get("Col") or bm.loops.layers.color.get("Color")
    if color_layer is None and bm.loops.layers.color:
        for name, layer in bm.loops.layers.color.items():
            color_layer = layer
            break
    if color_layer is None:
        color_layer = bm.loops.layers.color.new(attr_name)

    n_faces = len(bm.faces)
    log_interval = PROGRESS_LOG_INTERVAL if PROGRESS_LOG_INTERVAL > 0 else n_faces + 1
    for face_idx, face in enumerate(bm.faces):
        if (face_idx + 1) % log_interval == 0 or face_idx == 0 or face_idx == n_faces - 1:
            print(f"    頂点色書き戻し: {face_idx + 1}/{n_faces}")
        color_idx = assignments[face_idx] if face_idx < len(assignments) else 0
        c = palette[color_idx] if color_idx < len(palette) else (0.5, 0.5, 0.5)
        for loop in face.loops:
            loop[color_layer] = (c[0], c[1], c[2], 1.0)

    bm.to_mesh(mesh)
    mesh.update()
    bm.free()
    return True


def process_scene(output_path=None, report_fn=None):
    """選択メッシュを4色減色・分割し、3MF/OBJエクスポートする。output_path が None のときは OUTPUT_PATH を使用。"""
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    # 保存パス: 渡されていればそれを使用、なければ設定の OUTPUT_PATH
    effective_path = (output_path or OUTPUT_PATH).rstrip()
    # 拡張子からエクスポート形式を判定（.obj → OBJ、それ以外 → 3MF）
    export_format_from_path = "obj" if effective_path.lower().endswith(".obj") else "3mf"

    def report(msg):
        if report_fn:
            report_fn(msg)
        print(msg)

    report("4色減色・エクスポートを開始します")

    # 対象: USE_SELECTION_ONLY のときは選択メッシュのみ、そうでなければ全メッシュ
    if USE_SELECTION_ONLY:
        candidates = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not candidates:
            msg = "メッシュオブジェクトを1つ以上選択してから実行してください。（1体だけ出力する場合は1つだけ選択）"
            print(msg)
            if report_fn:
                report_fn(msg)
            return
        print(f"[調査] 処理対象: 選択されたメッシュ {len(candidates)} 個")
    else:
        if bpy.context.selected_objects:
            candidates = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        else:
            candidates = [o for o in scene.objects if o.type == "MESH"]
        print(f"[調査] 処理対象: メッシュ {len(candidates)} 個")
    if not candidates:
        msg = "メッシュオブジェクトがありません。"
        print(msg)
        if report_fn:
            report_fn(msg)
        return
    for i, o in enumerate(candidates):
        print(f"  - [{i}] {o.name}")

    # 表示色を頂点カラーにベイク（オプション）
    if BAKE_TO_VERTEX_COLOR and candidates:
        scene.render.engine = "CYCLES"
        for obj in candidates:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            view_layer.objects.active = obj
            print(f"  ベイク中: {obj.name} → 属性 \"{BAKE_TARGET_ATTR_NAME}\"")
            if bake_material_to_vertex_colors(obj, BAKE_TARGET_ATTR_NAME):
                print(f"    完了: {obj.name}")
                if report_fn:
                    report_fn(f"ベイク完了: {obj.name}")
            else:
                print(f"    スキップまたは失敗: {obj.name}")

    # EXPORT_MODE に応じて「分割」するか「頂点色のみ書き戻し」か
    created = []
    if EXPORT_MODE == "vertex_color_only":
        # 分割せず、減色した色を頂点カラーに書き戻すだけ（1メッシュのまま → 非多様体回避）
        print("[調査] モード: vertex_color_only（分割せず頂点色のみ）")
        for obj in candidates:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            view_layer.objects.active = obj
            has_vcol, face_colors = get_face_colors_from_mesh(obj)
            if not face_colors:
                continue
            palette, assignments = quantize_colors_kmeans(face_colors, k=NUM_COLORS, max_iter=KMEANS_ITERATIONS)
            if len(set(assignments)) < 2:
                assignments = [i % NUM_COLORS for i in range(len(assignments))]
                print("  頂点色が一色のため、面を均等に4色に割り当てました。")
            palette = ensure_distinct_palette(palette, k=NUM_COLORS)
            if apply_quantized_vertex_colors(obj, face_colors, assignments, palette):
                created.append(obj)
                print(f"  頂点色を適用: {obj.name}")
            else:
                print(f"  頂点色適用スキップ: {obj.name}")
        if not created:
            msg = "頂点色を適用できるメッシュがありません。"
            print(msg)
            if report_fn:
                report_fn(msg)
            return
        print(f"[調査] 頂点色を適用したオブジェクト: {len(created)} 個（メッシュは分割していません）")
        if report_fn:
            report_fn(f"減色・頂点色適用完了: {len(created)} オブジェクト")
    else:
        # 従来: 色ごとにメッシュ分割
        print("[調査] モード: split（色ごとにメッシュ分割）")
        for obj in candidates:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            view_layer.objects.active = obj
            has_vcol, face_colors = get_face_colors_from_mesh(obj)
            if not face_colors:
                continue
            palette, assignments = quantize_colors_kmeans(face_colors, k=NUM_COLORS, max_iter=KMEANS_ITERATIONS)
            if len(set(assignments)) < 2:
                assignments = [i % NUM_COLORS for i in range(len(assignments))]
                print("  頂点色が一色のため、面を均等に4分割しました。")
            palette = ensure_distinct_palette(palette, k=NUM_COLORS)
            new_objs = mesh_split_by_color(obj, face_colors, assignments, palette)
            created.extend(new_objs)
        if not created:
            msg = "分割できるメッシュがありません。"
            print(msg)
            if report_fn:
                report_fn(msg)
            return
        print(f"[調査] 作成した分割オブジェクト: {len(created)} 個（色ごと）")
        if report_fn:
            report_fn(f"減色・分割完了: {len(created)} オブジェクト")
    if EXPORT_MODE != "vertex_color_only":
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

    # 分割モード時のみ「元の候補オブジェクト」をシーンから外す（3MF に含めない）
    restored_collections = []  # [(obj_name, [col_name, ...]), ...] 名前で保存して参照無効化を回避
    if EXPORT_MODE == "split":
        for obj in candidates:
            cols = list(obj.users_collection)
            restored_collections.append((obj.name, [c.name for c in cols]))
            for c in cols:
                c.objects.unlink(obj)
        print("[調査] 元オブジェクトをコレクションから外しました（エクスポート後に復元します）")

    # エクスポート時は created を一時的に外し、コピーだけをシーンに残してエクスポート
    # 復元時は名前で再取得するため、参照無効化（StructRNA removed）を防ぐ
    created_collections = []  # [(obj_name, [col_name, ...]), ...]
    for obj in created:
        if hasattr(obj, "users_collection"):
            cols = list(obj.users_collection)
            created_collections.append((obj.name, [c.name for c in cols]))
            for c in cols:
                c.objects.unlink(obj)
        else:
            created_collections.append((getattr(obj, "name", ""), []))

    try:
        # エクスポート用にコピーを作成（Blender上の created はスケールをいじらない）
        # created の要素は Object 想定。Object は material_slots / .data を持つ。Mesh の場合は .materials のみ。
        export_objects = []
        export_object_names = []  # 削除時に参照無効化を避けるため名前を保持
        for obj in created:
            if hasattr(obj, "material_slots") and hasattr(obj, "data"):
                mesh_copy = obj.data.copy()
                obj_copy = bpy.data.objects.new(name=obj.name + "_export", object_data=mesh_copy)
                obj_copy.matrix_world = obj.matrix_world.copy()
                obj_copy.data.materials.clear()
                for slot in obj.material_slots:
                    if slot.material:
                        obj_copy.data.materials.append(slot.material)
            else:
                mesh_copy = obj.copy()
                obj_copy = bpy.data.objects.new(name=getattr(obj, "name", "mesh") + "_export", object_data=mesh_copy)
                obj_copy.matrix_world = __import__("mathutils").Matrix.Identity(4)
                obj_copy.data.materials.clear()
                for mat in getattr(obj, "materials", []):
                    if mat:
                        obj_copy.data.materials.append(mat)
            bpy.context.collection.objects.link(obj_copy)
            export_objects.append(obj_copy)
            export_object_names.append(obj_copy.name)

        # エクスポート用に created を一時外した分は上で created_collections に済み（重複ループ削除）

        # コピーだけ選択し、トランスフォームをメッシュに焼き込んでからスケール適用
        bpy.ops.object.select_all(action="DESELECT")
        for o in export_objects:
            o.select_set(True)
        if export_objects:
            view_layer.objects.active = export_objects[0]

        # ワールド空間に統一
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        # 出力スケール（10%）をメッシュに適用（Blender上のオブジェクトはそのまま）
        if EXPORT_SCALE != 1.0:
            for o in export_objects:
                o.scale *= EXPORT_SCALE
            bpy.ops.object.transform_apply(scale=True)

        # エクスポート形式に応じて 3MF または 頂点色付き OBJ を出力（形式は effective_path の拡張子で判定済み）
        if export_format_from_path == "obj":
            # 頂点色付き OBJ（Bambu Studio 等で色が認識される場合あり）
            obj_path = effective_path
            if not obj_path.lower().endswith(".obj"):
                base = obj_path.rsplit(".", 1)[0] if "." in obj_path else obj_path
                obj_path = base + ".obj"
            try:
                bpy.ops.wm.obj_export(filepath=obj_path, export_colors=True)
                print(f"減色された頂点色付きOBJをエクスポートしました: {obj_path}")
                if report_fn:
                    report_fn(f"エクスポート完了: {obj_path}")
            except TypeError:
                try:
                    bpy.ops.wm.obj_export(filepath=obj_path, export_vertex_colors=True)
                    print(f"減色された頂点色付きOBJをエクスポートしました: {obj_path}")
                    if report_fn:
                        report_fn(f"エクスポート完了: {obj_path}")
                except TypeError:
                    bpy.ops.wm.obj_export(filepath=obj_path)
                    print(f"OBJをエクスポートしました（頂点色オプションは未対応の可能性）: {obj_path}")
                    if report_fn:
                        report_fn(f"エクスポート完了: {obj_path}")
            except AttributeError:
                try:
                    bpy.ops.export_scene.obj(filepath=obj_path, use_selection=True, use_materials=False, export_colors=True)
                    print(f"減色された頂点色付きOBJをエクスポートしました: {obj_path}")
                    if report_fn:
                        report_fn(f"エクスポート完了: {obj_path}")
                except Exception as e:
                    print(f"OBJエクスポート失敗: {e}")
                    if report_fn:
                        report_fn(f"OBJエクスポート失敗: {e}")
        else:
            # 3MF エクスポート（アドオンで export_mesh.threemf が登録されている前提）
            try:
                bpy.ops.export_mesh.threemf(filepath=effective_path)
                print(f"減色された3MFファイルをエクスポートしました: {effective_path}")
                if report_fn:
                    report_fn(f"エクスポート完了: {effective_path}")
            except AttributeError:
                try:
                    bpy.ops.export_mesh.three_mf(filepath=effective_path)
                    print(f"減色された3MFファイルをエクスポートしました: {effective_path}")
                    if report_fn:
                        report_fn(f"エクスポート完了: {effective_path}")
                except AttributeError:
                    msg = "3MFエクスポートが見つかりません。Blenderに「3MF format」アドオンを有効にしてください。"
                    print(msg)
                    print("エクスポートパス:", effective_path)
                    if report_fn:
                        report_fn(msg)

        # エクスポート用コピーを削除（名前で再取得して参照無効化を回避）
        for name in export_object_names:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh and mesh.users == 0:
                    bpy.data.meshes.remove(mesh, do_unlink=True)
    finally:
        # 分割オブジェクト（created）をコレクションに戻す（名前で再取得。参照無効化時も落ちないよう try で保護）
        for obj_name, col_names in created_collections:
            try:
                obj = bpy.data.objects.get(obj_name)
                if obj is None:
                    continue
                for col_name in col_names:
                    c = bpy.data.collections.get(col_name)
                    if c is not None:
                        try:
                            c.objects.link(obj)
                        except (ReferenceError, RuntimeError):
                            pass
            except ReferenceError:
                pass
        for obj_name, col_names in restored_collections:
            try:
                obj = bpy.data.objects.get(obj_name)
                if obj is None:
                    continue
                for col_name in col_names:
                    c = bpy.data.collections.get(col_name)
                    if c is not None:
                        try:
                            c.objects.link(obj)
                        except (ReferenceError, RuntimeError):
                            pass
            except ReferenceError:
                pass
        print("[調査] 元オブジェクトをコレクションに戻しました。")


class EXPORT_OT_4color(bpy.types.Operator, ExportHelper):
    """4色減色して 3MF または頂点色付き OBJ でエクスポート（保存先をダイアログで指定）"""
    bl_idname = "export_4color.export"
    bl_label = "4色減色 3MF/OBJ をエクスポート"
    bl_options = {"REGISTER"}

    filename_ext = ""
    filter_glob: bpy.props.StringProperty(default="*.obj;*.3mf", options={"HIDDEN"})

    def execute(self, context):
        report_fn = lambda msg: self.report({"INFO"}, msg)
        process_scene(output_path=self.filepath, report_fn=report_fn)
        return {"FINISHED"}


def register():
    bpy.utils.register_class(EXPORT_OT_4color)


def unregister():
    bpy.utils.unregister_class(EXPORT_OT_4color)


if __name__ == "__main__":
    register()
    bpy.ops.export_4color.export("INVOKE_DEFAULT")

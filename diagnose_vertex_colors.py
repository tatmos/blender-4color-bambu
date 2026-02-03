# SPDX-License-Identifier: GPL-3.0-or-later
# 選択オブジェクトの頂点色・マテリアルを診断（Blender で実行）

import bpy
import bmesh

def diagnose():
    ob = bpy.context.active_object
    if not ob or ob.type != "MESH":
        print("メッシュオブジェクトを選択してから実行してください。")
        return

    mesh = ob.data
    print(f"--- 診断: {ob.name} ---")
    print(f"ポリゴン数: {len(mesh.polygons)}")

    # メッシュのカラー属性（Blender 4.0+ の属性名）
    if hasattr(mesh, "color_attributes") and mesh.color_attributes:
        for i, attr in enumerate(mesh.color_attributes):
            print(f"カラー属性[{i}] 名前: \"{attr.name}\"")
    else:
        print("メッシュに color_attributes がありません（旧形式の可能性）")

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    # bmesh の頂点色レイヤー（byte）
    if hasattr(bm.loops.layers, "color") and bm.loops.layers.color:
        for name, layer in bm.loops.layers.color.items():
            sample_r, sample_g, sample_b = 0.0, 0.0, 0.0
            n = 0
            for face in bm.faces:
                for loop in face.loops:
                    c = loop[layer]
                    sample_r += c[0]
                    sample_g += c[1]
                    sample_b += c[2]
                    n += 1
                    if n >= 300:
                        break
                if n >= 300:
                    break
            if n:
                sample_r /= n
                sample_g /= n
                sample_b /= n
            print(f"  bmesh 頂点色レイヤー(byte): \"{name}\" (サンプル平均 RGB: {sample_r:.3f}, {sample_g:.3f}, {sample_b:.3f})")
    # float color (Blender 4.0+ の属性によってはこちら)
    if hasattr(bm.loops.layers, "float_color") and bm.loops.layers.float_color:
        for name, layer in bm.loops.layers.float_color.items():
            sample_r, sample_g, sample_b = 0.0, 0.0, 0.0
            n = 0
            for face in bm.faces:
                for loop in face.loops:
                    c = loop[layer]
                    sample_r += c[0]
                    sample_g += c[1]
                    sample_b += c[2]
                    n += 1
                    if n >= 300:
                        break
                if n >= 300:
                    break
            if n:
                sample_r /= n
                sample_g /= n
                sample_b /= n
            print(f"  bmesh 頂点色レイヤー(float): \"{name}\" (サンプル平均 RGB: {sample_r:.3f}, {sample_g:.3f}, {sample_b:.3f})")
    if not (getattr(bm.loops.layers, "color", None) and bm.loops.layers.color) and \
       not (getattr(bm.loops.layers, "float_color", None) and bm.loops.layers.float_color):
        print("  bmesh に頂点色レイヤーがありません → マテリアルで色分けされます。")

    # マテリアル
    if ob.material_slots:
        print(f"マテリアル数: {len(ob.material_slots)}")
        for i, slot in enumerate(ob.material_slots):
            mat = slot.material
            if mat and mat.use_nodes:
                base = (0.5, 0.5, 0.5)
                for n in mat.node_tree.nodes:
                    if n.type == "BSDF_PRINCIPLED":
                        base = n.inputs["Base Color"].default_value[:3]
                        break
                print(f"  マテリアル[{i}] \"{mat.name}\" BaseColor: R={base[0]:.3f} G={base[1]:.3f} B={base[2]:.3f}")
            else:
                print(f"  マテリアル[{i}] \"{mat.name if mat else 'None'}\" (ノードなし)")
    else:
        print("マテリアルスロットがありません。")

    bm.free()
    print("--- 診断終了 ---")

if __name__ == "__main__":
    diagnose()

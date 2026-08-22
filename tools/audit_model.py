import bpy
import bmesh
import csv
import json
import math
import os
import re
import statistics
import sys
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def args_after_double_dash():
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1:]


def safe_name(value):
    return re.sub(r"[^0-9A-Za-zА-Яа-я_\-.]+", "_", value or "unnamed")[:180]


def world_bbox(obj):
    pts = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mins = [min(p[i] for p in pts) for i in range(3)]
    maxs = [max(p[i] for p in pts) for i in range(3)]
    return mins, maxs


def classify(name):
    n = (name or "").lower()
    groups = [
        ("terrain", ["terrain", "relief", "ground", "topo", "land", "рельеф", "грунт", "земл"]),
        ("road", ["road", "drive", "asphalt", "pavement", "serp", "дорог", "проезд", "серпант", "площадк", "parking"]),
        ("retaining_wall", ["retaining", "wall", "gabion", "подпор", "стен"]),
        ("building", ["building", "bldg", "warehouse", "hall", "корпус", "цех", "склад", "абк", "кпп", "насосн", "энерго"]),
        ("foundation", ["foundation", "pile", "raft", "footing", "фундамент", "свая", "роствер"]),
        ("pipe_utility", ["pipe", "pipeline", "utility", "network", "cable", "tray", "труб", "сеть", "кабел", "эстакад", "lotok"]),
        ("water", ["water", "pond", "drain", "reservoir", "storm", "пруд", "вода", "водо", "лос", "ливн"]),
        ("tank_equipment", ["tank", "vessel", "equipment", "reactor", "резервуар", "емк", "реактор", "оборуд"]),
        ("fence_site", ["fence", "gate", "barrier", "ограж", "ворот", "шлагбаум"]),
        ("landscape", ["tree", "vegetation", "grass", "landscape", "дерев", "озелен"]),
        ("vehicle", ["truck", "car", "vehicle", "авто", "машин", "трал"]),
        ("annotation", ["label", "text", "dimension", "axis", "grid", "подпис", "размер", "ось"]),
    ]
    for group, keys in groups:
        if any(k in n for k in keys):
            return group
    return "unclassified"


def mesh_stats(obj):
    if obj.type != "MESH" or obj.data is None:
        return {"verts": 0, "edges": 0, "faces": 0, "non_manifold_edges": 0, "loose_verts": 0}
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    loose = sum(1 for v in bm.verts if not v.link_edges)
    out = {
        "verts": len(bm.verts),
        "edges": len(bm.edges),
        "faces": len(bm.faces),
        "non_manifold_edges": non_manifold,
        "loose_verts": loose,
    }
    bm.free()
    return out


def build_bvh(obj, depsgraph):
    if obj.type != "MESH":
        return None
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    verts = [obj.matrix_world @ v.co for v in mesh.vertices]
    polys = [tuple(p.vertices) for p in mesh.polygons]
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False)
    eval_obj.to_mesh_clear()
    return bvh


def terrain_z(bvh, x, y, z_top):
    if bvh is None:
        return None
    hit = bvh.ray_cast(Vector((x, y, z_top)), Vector((0, 0, -1)), 10000.0)
    if hit and hit[0] is not None:
        return float(hit[0].z)
    return None


def aabb_overlap(a, b, tol=0.0):
    amin, amax = a
    bmin, bmax = b
    return all(amax[i] > bmin[i] + tol and bmax[i] > amin[i] + tol for i in range(3))


def xy_overlap_ratio(a, b):
    amin, amax = a; bmin, bmax = b
    ix = max(0.0, min(amax[0], bmax[0]) - max(amin[0], bmin[0]))
    iy = max(0.0, min(amax[1], bmax[1]) - max(amin[1], bmin[1]))
    inter = ix * iy
    aa = max(1e-9, (amax[0]-amin[0])*(amax[1]-amin[1]))
    bb = max(1e-9, (bmax[0]-bmin[0])*(bmax[1]-bmin[1]))
    return inter / min(aa, bb)


def configure_render(scene, objects, out_dir):
    mins = [1e30,1e30,1e30]; maxs = [-1e30,-1e30,-1e30]
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        try:
            mn, mx = world_bbox(obj)
        except Exception:
            continue
        for i in range(3): mins[i] = min(mins[i], mn[i]); maxs[i] = max(maxs[i], mx[i])
    center = Vector(tuple((mins[i]+maxs[i])*0.5 for i in range(3)))
    size = max(maxs[0]-mins[0], maxs[1]-mins[1], 10.0)

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.studio_light = "paint.sl"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.show_specular_highlight = True
    scene.display.shading.background_type = "WORLD"
    scene.display.shading.background_color = (0.035, 0.045, 0.06)
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    cam_data = bpy.data.cameras.new("AUDIT_CAMERA")
    cam = bpy.data.objects.new("AUDIT_CAMERA", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    def point_camera(location, target, ortho=False, ortho_scale=None):
        cam.location = Vector(location)
        direction = Vector(target) - cam.location
        cam.rotation_euler = direction.to_track_quat('-Z','Y').to_euler()
        if ortho:
            cam.data.type = 'ORTHO'; cam.data.ortho_scale = ortho_scale or size*1.2
        else:
            cam.data.type = 'PERSP'; cam.data.lens = 50

    views = {
        "01_top": (center + Vector((0,0,size*1.7)), center, True),
        "02_iso_sw": (center + Vector((-size*0.85,-size*0.85,size*0.75)), center, False),
        "03_iso_ne": (center + Vector((size*0.85,size*0.85,size*0.75)), center, False),
        "04_iso_se": (center + Vector((size*0.85,-size*0.85,size*0.60)), center, False),
        "05_low_west": (center + Vector((-size*0.95,0,size*0.20)), center + Vector((0,0,size*0.08)), False),
    }
    for name, (loc, target, ortho) in views.items():
        point_camera(loc, target, ortho, size*1.18)
        scene.render.filepath = os.path.join(out_dir, name + ".png")
        bpy.ops.render.render(write_still=True)

    # Wireframe top view for exterior debugging.
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.62,0.72,0.88)
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = False
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.show_wireframes = True
    point_camera(center + Vector((0,0,size*1.7)), center, True, size*1.18)
    scene.render.filepath = os.path.join(out_dir, "06_top_wireframe.png")
    bpy.ops.render.render(write_still=True)


def main():
    args = args_after_double_dash()
    if len(args) < 2:
        raise SystemExit("Usage: blender -b --python audit_model.py -- INPUT.glb OUTPUT_DIR")
    input_glb = os.path.abspath(args[0]); out_dir = os.path.abspath(args[1])
    os.makedirs(out_dir, exist_ok=True)

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=input_glb, import_pack_images=True, merge_vertices=False)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0
    objects = [o for o in scene.objects if o.name != "AUDIT_CAMERA"]
    depsgraph = bpy.context.evaluated_depsgraph_get()

    inventory = []
    bboxes = {}
    category_counts = {}
    for obj in objects:
        try:
            mn, mx = world_bbox(obj)
            dims = [mx[i]-mn[i] for i in range(3)]
        except Exception:
            mn = mx = dims = [0,0,0]
        cat = classify(obj.name)
        category_counts[cat] = category_counts.get(cat,0)+1
        ms = mesh_stats(obj)
        row = {
            "name": obj.name,
            "type": obj.type,
            "category": cat,
            "parent": obj.parent.name if obj.parent else "",
            "min_x": mn[0], "min_y": mn[1], "min_z": mn[2],
            "max_x": mx[0], "max_y": mx[1], "max_z": mx[2],
            "dim_x": dims[0], "dim_y": dims[1], "dim_z": dims[2],
            "loc_x": obj.location.x, "loc_y": obj.location.y, "loc_z": obj.location.z,
            "rot_x": obj.rotation_euler.x, "rot_y": obj.rotation_euler.y, "rot_z": obj.rotation_euler.z,
            "scale_x": obj.scale.x, "scale_y": obj.scale.y, "scale_z": obj.scale.z,
            **ms,
        }
        inventory.append(row); bboxes[obj.name] = (mn,mx)

    mesh_objs = [o for o in objects if o.type == "MESH"]
    terrain_candidates = []
    for obj in mesh_objs:
        mn,mx = bboxes[obj.name]; dx=mx[0]-mn[0]; dy=mx[1]-mn[1]; dz=mx[2]-mn[2]
        score = dx*dy / max(1.0,dz)
        if classify(obj.name)=="terrain": score *= 20.0
        terrain_candidates.append((score,obj))
    terrain_candidates.sort(key=lambda x:x[0], reverse=True)
    terrain_obj = terrain_candidates[0][1] if terrain_candidates else None
    terrain_bvh = build_bvh(terrain_obj,depsgraph) if terrain_obj else None
    global_top = max((r["max_z"] for r in inventory), default=100.0) + 1000.0

    issues = []
    issue_id = 1
    def add_issue(kind,severity,obj_names,description,metrics=None,recommendation=""):
        nonlocal issue_id
        issues.append({"id":f"EXT-{issue_id:03d}","kind":kind,"severity":severity,"objects":obj_names,"description":description,"metrics":metrics or {},"recommendation":recommendation})
        issue_id += 1

    # Object-level exterior checks.
    exterior_categories = {"road","retaining_wall","building","foundation","pipe_utility","water","tank_equipment","fence_site","unclassified"}
    for row in inventory:
        if row["category"] not in exterior_categories or row["type"] != "MESH":
            continue
        dims = sorted([row["dim_x"],row["dim_y"],row["dim_z"]], reverse=True)
        if dims[0] > 80 and dims[1] < 0.35 and row["category"] not in {"pipe_utility","fence_site"}:
            add_issue("stray_long_thin_geometry","high",[row["name"]],"Обнаружен аномально длинный и тонкий объект — вероятная случайная линия/ось/обрывок геометрии.",{"dimensions":dims},"Удалить либо перевести в аннотационный слой, если объект не является конструкцией.")
        scale = [row["scale_x"],row["scale_y"],row["scale_z"]]
        if any(s < 0 for s in scale):
            add_issue("negative_scale","medium",[row["name"]],"Объект имеет отрицательный масштаб; возможны перевернутые нормали и артефакты экспорта.",{"scale":scale},"Применить трансформации и пересчитать нормали.")
        if any(abs(s-1.0)>0.02 for s in scale):
            add_issue("unapplied_scale","low",[row["name"]],"Масштаб объекта не применен.",{"scale":scale},"Применить scale перед итоговым экспортом.")
        if row["non_manifold_edges"] > max(50, row["edges"]*0.10):
            add_issue("non_manifold_mesh","medium",[row["name"]],"Высокая доля неманифолдных ребер; возможны щели и визуальные разрывы.",{"non_manifold_edges":row["non_manifold_edges"],"edges":row["edges"]},"Проверить открытые границы, удалить дубли и объединить вершины.")

        if terrain_bvh and row["category"] in {"road","building","retaining_wall","tank_equipment","fence_site","unclassified"}:
            mn,mx=bboxes[row["name"]]
            samples=[((mn[0]+mx[0])*0.5,(mn[1]+mx[1])*0.5), (mn[0],mn[1]),(mn[0],mx[1]),(mx[0],mn[1]),(mx[0],mx[1])]
            tz=[terrain_z(terrain_bvh,x,y,global_top) for x,y in samples]
            tz=[v for v in tz if v is not None]
            if tz:
                med=statistics.median(tz); delta=row["min_z"]-med
                if delta>2.0 and row["category"] not in {"retaining_wall"}:
                    add_issue("floating_above_terrain","high",[row["name"]],"Наружный объект визуально висит над поверхностью/террасой.",{"base_z":row["min_z"],"terrain_z":med,"delta":delta},"Опустить объект или сформировать корректную площадку/опорную конструкцию.")
                elif delta<-2.0 and row["category"] in {"road","building","tank_equipment","fence_site"}:
                    add_issue("embedded_in_terrain","high",[row["name"]],"Наружный объект существенно погружен в поверхность.",{"base_z":row["min_z"],"terrain_z":med,"delta":delta},"Поднять объект либо вырезать площадку в рельефе и оформить откос/подпорную стену.")

    # Duplicate / near-duplicate bounding boxes.
    candidates=[r for r in inventory if r["type"]=="MESH" and r["category"] in exterior_categories]
    for i,a in enumerate(candidates):
        for b in candidates[i+1:]:
            ca=[(a[f"min_{k}"]+a[f"max_{k}"])*0.5 for k in "xyz"]
            cb=[(b[f"min_{k}"]+b[f"max_{k}"])*0.5 for k in "xyz"]
            da=[a[f"dim_{k}"] for k in "xyz"]; db=[b[f"dim_{k}"] for k in "xyz"]
            cdist=math.sqrt(sum((ca[j]-cb[j])**2 for j in range(3)))
            ddim=max(abs(da[j]-db[j]) for j in range(3))
            if cdist<0.05 and ddim<0.05:
                add_issue("duplicate_geometry","high",[a["name"],b["name"]],"Практически совпадающие наружные объекты создают мерцание и z-fighting.",{"center_distance":cdist,"max_dimension_delta":ddim},"Удалить дубль или объединить геометрию.")

    # Road/building and road/wall overlaps in bounding boxes.
    road_rows=[r for r in candidates if r["category"]=="road"]
    built_rows=[r for r in candidates if r["category"] in {"building","tank_equipment"}]
    wall_rows=[r for r in candidates if r["category"]=="retaining_wall"]
    for road in road_rows:
        for built in built_rows:
            if aabb_overlap(bboxes[road["name"]],bboxes[built["name"]],tol=0.05):
                ratio=xy_overlap_ratio(bboxes[road["name"]],bboxes[built["name"]])
                if ratio>0.03:
                    add_issue("road_building_overlap","high",[road["name"],built["name"]],"Проезжая часть пересекает объем здания/резервуара.",{"xy_overlap_ratio":ratio},"Скорректировать трассу дороги, площадку здания либо отметки террас.")
        for wall in wall_rows:
            if aabb_overlap(bboxes[road["name"]],bboxes[wall["name"]],tol=0.05):
                ratio=xy_overlap_ratio(bboxes[road["name"]],bboxes[wall["name"]])
                if ratio>0.08:
                    add_issue("road_wall_overlap","medium",[road["name"],wall["name"]],"Дорожная геометрия глубоко пересекает подпорную стену.",{"xy_overlap_ratio":ratio},"Согласовать бровку, парапет и отметку проезжей части.")

    inventory_path=os.path.join(out_dir,"object_inventory.csv")
    with open(inventory_path,"w",newline="",encoding="utf-8-sig") as f:
        fields=list(inventory[0].keys()) if inventory else ["name"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(inventory)

    audit={
        "input":os.path.basename(input_glb),
        "blender_version":bpy.app.version_string,
        "object_count":len(objects),
        "mesh_count":len(mesh_objs),
        "category_counts":category_counts,
        "terrain_candidate":terrain_obj.name if terrain_obj else None,
        "issue_count":len(issues),
        "severity_counts":{s:sum(1 for i in issues if i["severity"]==s) for s in ["critical","high","medium","low"]},
        "issues":issues,
    }
    with open(os.path.join(out_dir,"audit.json"),"w",encoding="utf-8") as f:
        json.dump(audit,f,ensure_ascii=False,indent=2)
    with open(os.path.join(out_dir,"issues.csv"),"w",newline="",encoding="utf-8-sig") as f:
        fields=["id","severity","kind","objects","description","metrics","recommendation"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for issue in issues:
            w.writerow({**issue,"objects":" | ".join(issue["objects"]),"metrics":json.dumps(issue["metrics"],ensure_ascii=False)})

    configure_render(scene,objects,out_dir)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir,"audit_scene.blend"))
    print(json.dumps({"status":"ok","objects":len(objects),"issues":len(issues),"terrain":audit["terrain_candidate"]},ensure_ascii=False))


if __name__ == "__main__":
    main()

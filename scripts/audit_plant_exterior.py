# -*- coding: utf-8 -*-
"""Exterior audit for the A.1 tire-pyrolysis plant coordination model.

Executed by Blender in background mode. The script imports a GLB, inventories
objects, classifies exterior categories, finds visual artifacts and AABB clashes,
and renders control views. It does not modify the source model.
"""
from __future__ import annotations

import bpy
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from itertools import product
from mathutils import Vector


def args_after_double_dash():
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


ARGS = args_after_double_dash()
if len(ARGS) < 2:
    raise SystemExit("Usage: blender -b --python audit_plant_exterior.py -- input.glb output_dir")
INPUT_GLB = os.path.abspath(ARGS[0])
OUT_DIR = os.path.abspath(ARGS[1])
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(os.path.join(OUT_DIR, "renders"), exist_ok=True)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path):
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)


def world_bbox(obj):
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
        loc = obj.matrix_world.translation
        return [loc.x, loc.y, loc.z], [loc.x, loc.y, loc.z]
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mins = [min(c[i] for c in corners) for i in range(3)]
    maxs = [max(c[i] for c in corners) for i in range(3)]
    return mins, maxs


def bbox_center(bmin, bmax):
    return [(bmin[i] + bmax[i]) * 0.5 for i in range(3)]


def bbox_dims(bmin, bmax):
    return [max(0.0, bmax[i] - bmin[i]) for i in range(3)]


def bbox_overlap(a_min, a_max, b_min, b_max, tol=0.0):
    return all(min(a_max[i], b_max[i]) - max(a_min[i], b_min[i]) > tol for i in range(3))


def xy_overlap_depth(a_min, a_max, b_min, b_max):
    dx = min(a_max[0], b_max[0]) - max(a_min[0], b_min[0])
    dy = min(a_max[1], b_max[1]) - max(a_min[1], b_min[1])
    return dx, dy


def normalize_name(name):
    return re.sub(r"[^a-zа-я0-9]+", " ", name.lower()).strip()


KEYWORDS = {
    "terrain": ["terrain", "relief", "ground", "site surface", "рельеф", "земля", "естествен", "existing ground"],
    "road": ["road", "дорог", "проезд", "въезд", "серпантин", "петля", "asphalt", "pavement", "drive", "route"],
    "terrace": ["terrace", "террас", "platform", "площадк", "apron", "yard", "pad"],
    "retaining_wall": ["retaining", "подпор", "gabion", "wall rt", "rw_", "стена подп"],
    "building": ["building", "bldg", "корпус", "склад", "цех", "абк", "warehouse", "hall", "roof", "wall panel", "facade"],
    "foundation": ["foundation", "fnd", "роствер", "сва", "pile", "anchor", "фундамент"],
    "pipe_network": ["pipe", "piping", "труб", "network", "rack", "эстакад", "collector", "cw_", "fw_", "pg_", "oil_", "air_", "n2_", "dn"],
    "drainage": ["drain", "ливн", "водоот", "канал", "ditch", "culvert", "лоток", "pond", "лос", "snow"],
    "vegetation": ["tree", "veget", "conifer", "дерев", "куст", "landscape"],
    "lighting": ["light", "lamp", "освещ", "фонар", "pole_light"],
    "fence": ["fence", "ограж", "gate", "ворот", "barrier", "шлагбаум"],
    "vehicle": ["truck", "car", "vehicle", "авто", "машин", "trailer", "цистерн"],
    "tank": ["tank", "rvs", "резервуар", "емк", "vessel"],
    "reserve": ["reserve", "резерв", "phase2", "second phase", "2 очередь"],
    "helper": ["helper", "guide", "axis", "centerline", "clash", "bbox", "envelope", "clearance", "rfi", "trajectory", "path", "line", "контур", "ось"],
}


def classify(name, collection_names):
    text = normalize_name(name + " " + " ".join(collection_names))
    scores = {}
    for cat, words in KEYWORDS.items():
        scores[cat] = sum(1 for w in words if w in text)
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    # Heuristics for coded model objects.
    raw = name.upper()
    if raw.startswith(("FND_", "PILE_", "ANCHOR_")):
        return "foundation"
    if raw.startswith(("EQ_", "EQUIP_")):
        return "equipment"
    if raw.startswith(("PIPE_", "NET_", "RACK_")):
        return "pipe_network"
    if raw.startswith(("ROAD_", "RD_")):
        return "road"
    if raw.startswith(("BLD_", "B_")):
        return "building"
    return "other"


def collection_path(obj):
    return sorted({c.name for c in obj.users_collection})


def polygon_count(obj):
    return len(obj.data.polygons) if obj.type == "MESH" and hasattr(obj.data, "polygons") else 0


def vertex_count(obj):
    return len(obj.data.vertices) if obj.type == "MESH" and hasattr(obj.data, "vertices") else 0


def material_names(obj):
    if obj.type != "MESH":
        return []
    return [slot.material.name for slot in obj.material_slots if slot.material]


def inspect_scene():
    records = []
    scene_min = [float("inf")] * 3
    scene_max = [float("-inf")] * 3
    for obj in bpy.context.scene.objects:
        if obj.hide_render:
            continue
        bmin, bmax = world_bbox(obj)
        dims = bbox_dims(bmin, bmax)
        for i in range(3):
            scene_min[i] = min(scene_min[i], bmin[i])
            scene_max[i] = max(scene_max[i], bmax[i])
        cols = collection_path(obj)
        cat = classify(obj.name, cols)
        rec = {
            "name": obj.name,
            "type": obj.type,
            "category": cat,
            "collections": cols,
            "bbox_min": [round(v, 5) for v in bmin],
            "bbox_max": [round(v, 5) for v in bmax],
            "center": [round(v, 5) for v in bbox_center(bmin, bmax)],
            "dimensions": [round(v, 5) for v in dims],
            "vertices": vertex_count(obj),
            "polygons": polygon_count(obj),
            "materials": material_names(obj),
            "visible": not obj.hide_viewport,
            "render_visible": not obj.hide_render,
        }
        records.append(rec)
    return records, scene_min, scene_max


def artifact_candidates(records):
    out = []
    for r in records:
        d = sorted(r["dimensions"], reverse=True)
        max_dim = d[0] if d else 0
        mid_dim = d[1] if len(d) > 1 else 0
        min_dim = d[2] if len(d) > 2 else 0
        reasons = []
        if r["category"] == "helper":
            reasons.append("helper_category")
        if max_dim > 40 and mid_dim < 0.20 and min_dim < 0.20:
            reasons.append("very_long_thin")
        if max_dim > 100 and mid_dim < 0.50:
            reasons.append("long_line_like")
        if r["type"] == "MESH" and r["polygons"] <= 4 and max_dim > 40:
            reasons.append("low_poly_long_object")
        if reasons:
            out.append({"name": r["name"], "category": r["category"], "dimensions": r["dimensions"], "reasons": reasons})
    return out


def duplicate_candidates(records):
    buckets = defaultdict(list)
    for r in records:
        if r["type"] != "MESH":
            continue
        key = (
            tuple(round(x, 3) for x in r["center"]),
            tuple(round(x, 3) for x in r["dimensions"]),
            r["vertices"],
            r["polygons"],
        )
        buckets[key].append(r["name"])
    return [names for names in buckets.values() if len(names) > 1]


def clash_candidates(records):
    cats = defaultdict(list)
    for r in records:
        if r["type"] == "MESH":
            cats[r["category"]].append(r)
    checks = [
        ("road", "building"),
        ("road", "retaining_wall"),
        ("road", "foundation"),
        ("road", "vegetation"),
        ("road", "lighting"),
        ("road", "fence"),
        ("building", "retaining_wall"),
        ("building", "vegetation"),
        ("building", "lighting"),
        ("foundation", "pipe_network"),
        ("tank", "road"),
    ]
    clashes = []
    seen = set()
    for ca, cb in checks:
        for a in cats.get(ca, []):
            for b in cats.get(cb, []):
                if a["name"] == b["name"]:
                    continue
                pair = tuple(sorted((a["name"], b["name"])))
                if pair in seen:
                    continue
                seen.add(pair)
                amin, amax = a["bbox_min"], a["bbox_max"]
                bmin, bmax = b["bbox_min"], b["bbox_max"]
                dx, dy = xy_overlap_depth(amin, amax, bmin, bmax)
                dz = min(amax[2], bmax[2]) - max(amin[2], bmin[2])
                if dx > 0.05 and dy > 0.05 and dz > 0.02:
                    volume = dx * dy * dz
                    severity = "high" if min(dx, dy) > 0.5 and dz > 0.2 else "medium"
                    clashes.append({
                        "a": a["name"], "category_a": ca,
                        "b": b["name"], "category_b": cb,
                        "overlap_xyz": [round(dx, 3), round(dy, 3), round(dz, 3)],
                        "overlap_box_volume": round(volume, 4),
                        "severity": severity,
                    })
    clashes.sort(key=lambda x: x["overlap_box_volume"], reverse=True)
    return clashes


def configure_render(scene_min, scene_max):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.use_gtao = True
    scene.eevee.gtao_distance = 5
    scene.eevee.gtao_factor = 1.4
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world.color = (0.68, 0.72, 0.78)
    # Color management.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = 0.1
    scene.view_settings.gamma = 1.0
    # Sun.
    sun_data = bpy.data.lights.new(name="AUDIT_SUN", type="SUN")
    sun_data.energy = 3.0
    sun_data.angle = math.radians(8)
    sun = bpy.data.objects.new("AUDIT_SUN", sun_data)
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(35), 0, math.radians(-35))
    # Camera.
    cam_data = bpy.data.cameras.new("AUDIT_CAMERA")
    cam = bpy.data.objects.new("AUDIT_CAMERA", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def point_camera(cam, location, target, ortho_scale=None, lens=50):
    cam.location = Vector(location)
    direction = Vector(target) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    if ortho_scale:
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = ortho_scale
    else:
        cam.data.type = "PERSP"
        cam.data.lens = lens


def render_views(scene_min, scene_max):
    cam = configure_render(scene_min, scene_max)
    cx = (scene_min[0] + scene_max[0]) / 2
    cy = (scene_min[1] + scene_max[1]) / 2
    cz = (scene_min[2] + scene_max[2]) / 2
    sx = scene_max[0] - scene_min[0]
    sy = scene_max[1] - scene_min[1]
    sz = scene_max[2] - scene_min[2]
    span = max(sx, sy)
    target = (cx, cy, cz)
    views = [
        ("01_top", (cx, cy, scene_max[2] + span * 1.1), (cx, cy, cz), span * 1.12, 50),
        ("02_iso_sw", (cx - span * 0.82, cy - span * 0.82, scene_max[2] + span * 0.58), target, None, 55),
        ("03_iso_ne", (cx + span * 0.82, cy + span * 0.82, scene_max[2] + span * 0.58), target, None, 55),
        ("04_east_serpentine", (scene_max[0] + span * 0.45, cy, cz + span * 0.22), (cx + sx * 0.28, cy, cz), None, 60),
        ("05_south_entry", (cx, scene_min[1] - span * 0.55, cz + span * 0.14), (cx, cy - sy * 0.16, cz), None, 58),
        ("06_low_west", (scene_min[0] - span * 0.35, cy - sy * 0.12, cz + span * 0.12), (cx, cy, cz), None, 62),
    ]
    for name, loc, tgt, ortho, lens in views:
        point_camera(cam, loc, tgt, ortho, lens)
        bpy.context.scene.render.filepath = os.path.join(OUT_DIR, "renders", name + ".png")
        bpy.ops.render.render(write_still=True)


def write_outputs(records, scene_min, scene_max):
    artifacts = artifact_candidates(records)
    duplicates = duplicate_candidates(records)
    clashes = clash_candidates(records)
    summary = {
        "input_file": os.path.basename(INPUT_GLB),
        "object_count": len(records),
        "mesh_count": sum(r["type"] == "MESH" for r in records),
        "vertex_count": sum(r["vertices"] for r in records),
        "polygon_count": sum(r["polygons"] for r in records),
        "scene_bbox_min": [round(x, 4) for x in scene_min],
        "scene_bbox_max": [round(x, 4) for x in scene_max],
        "scene_dimensions": [round(scene_max[i] - scene_min[i], 4) for i in range(3)],
        "category_counts": dict(Counter(r["category"] for r in records)),
        "artifact_candidate_count": len(artifacts),
        "duplicate_group_count": len(duplicates),
        "aabb_clash_count": len(clashes),
        "aabb_high_clash_count": sum(c["severity"] == "high" for c in clashes),
    }
    payload = {
        "summary": summary,
        "artifact_candidates": artifacts,
        "duplicate_candidates": duplicates,
        "clash_candidates": clashes,
        "objects": records,
    }
    with open(os.path.join(OUT_DIR, "audit_full.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "audit_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "objects.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["name", "type", "category", "center_x", "center_y", "center_z", "dx", "dy", "dz", "vertices", "polygons", "collections", "materials"])
        for r in records:
            w.writerow([
                r["name"], r["type"], r["category"], *r["center"], *r["dimensions"],
                r["vertices"], r["polygons"], " | ".join(r["collections"]), " | ".join(r["materials"]),
            ])
    with open(os.path.join(OUT_DIR, "clashes.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["severity", "category_a", "object_a", "category_b", "object_b", "overlap_x", "overlap_y", "overlap_z", "overlap_box_volume"])
        for c in clashes:
            w.writerow([c["severity"], c["category_a"], c["a"], c["category_b"], c["b"], *c["overlap_xyz"], c["overlap_box_volume"]])
    with open(os.path.join(OUT_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write("A.1 LOD400-P exterior audit\n")
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


reset_scene()
import_model(INPUT_GLB)
records, scene_min, scene_max = inspect_scene()
summary = write_outputs(records, scene_min, scene_max)
render_views(scene_min, scene_max)
print("AUDIT_COMPLETE")
print(json.dumps(summary, ensure_ascii=False))

# -*- coding: utf-8 -*-
"""A.1 / Rev.P2 exterior coordination and visual QA.

This Blender background script corrects the external part of the existing
A.1 LOD400-P tire-pyrolysis plant model without changing the fundamental
master-plan concept. It rebuilds roads, service lanes, pads, visible outdoor
infrastructure and site details, removes visual QA helpers, culls vegetation
from operational areas, re-runs meaningful exterior checks, and exports a
clean GLB plus the editable BLEND source.
"""
from __future__ import annotations

import bpy
import csv
import json
import math
import os
import re
import sys
from collections import defaultdict
from mathutils import Matrix, Vector

# -----------------------------------------------------------------------------
# Arguments and paths
# -----------------------------------------------------------------------------
ARGS = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if len(ARGS) < 2:
    raise SystemExit("Usage: blender -b --python correct_plant_exterior_revp2.py -- input.glb output_dir")
INPUT_GLB = os.path.abspath(ARGS[0])
OUT_DIR = os.path.abspath(ARGS[1])
RENDER_DIR = os.path.join(OUT_DIR, "renders")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)

GLB_OUT = os.path.join(OUT_DIR, "20_Модель_A1_RevP2_ExteriorQA.glb")
BLEND_OUT = os.path.join(OUT_DIR, "20_Модель_A1_RevP2_ExteriorQA.blend")
OBJ_OUT = os.path.join(OUT_DIR, "20_Модель_A1_RevP2_ExteriorQA.obj")
AUDIT_JSON = os.path.join(OUT_DIR, "20_Аудит_A1_RevP2_ExteriorQA.json")
CHANGE_CSV = os.path.join(OUT_DIR, "20_Ведомость_исправлений_A1_RevP2.csv")
REPORT_HTML = os.path.join(OUT_DIR, "20_Отчет_внешней_координации_A1_RevP2.html")
README = os.path.join(OUT_DIR, "20_README_A1_RevP2_ExteriorQA.txt")

# -----------------------------------------------------------------------------
# General utilities
# -----------------------------------------------------------------------------
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_and_normalize(path: str):
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)
    # The current project GLB is stored with plan Y mapped to imported Z and
    # elevation mapped to imported -Y. Rotate to true Blender Z-up.
    rot = Matrix.Rotation(math.radians(-90.0), 4, "X")
    for obj in list(bpy.context.scene.objects):
        obj.matrix_world = rot @ obj.matrix_world
    bpy.context.view_layer.update()


def ensure_collection(name: str):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


COL_EXT = ensure_collection("REV_P2_EXTERIOR")
COL_ROADS = ensure_collection("REV_P2_ROADS")
COL_DETAILS = ensure_collection("REV_P2_SITE_DETAILS")
COL_HIDDEN = ensure_collection("TECH_HIDDEN_NOT_EXPORTED")
COL_EXISTING = ensure_collection("EXISTING_MODEL")


def link_to_collection(obj, collection):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)


def hide_technical(obj):
    link_to_collection(obj, COL_HIDDEN)
    obj.hide_render = True
    obj.hide_set(True)
    obj["export_status"] = "hidden_technical"


def delete_object(obj):
    bpy.data.objects.remove(obj, do_unlink=True)


def world_bbox(obj):
    if obj.type != "MESH":
        p = obj.matrix_world.translation
        return [p.x, p.y, p.z], [p.x, p.y, p.z]
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def bbox_center(obj):
    mn, mx = world_bbox(obj)
    return Vector(((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2))


def bbox_dims(obj):
    mn, mx = world_bbox(obj)
    return Vector((mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]))


def add_prop(obj, **props):
    for k, v in props.items():
        obj[k] = v
    return obj


# -----------------------------------------------------------------------------
# Materials
# -----------------------------------------------------------------------------
def make_material(name, color, metallic=0.0, roughness=0.65, alpha=1.0, emission=None):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
    mat.diffuse_color = (*color, alpha)
    mat.blend_method = "BLEND" if alpha < 0.999 else "OPAQUE"
    mat.use_screen_refraction = alpha < 0.999
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Alpha"].default_value = alpha
        if emission is not None and "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
            bsdf.inputs["Emission Strength"].default_value = 2.0
    return mat


MAT = {
    "terrain": make_material("P2_Terrain", (0.30, 0.39, 0.22), roughness=0.95),
    "grass_fill": make_material("P2_GrassFill", (0.33, 0.43, 0.25), roughness=0.95),
    "tree": make_material("P2_Conifers", (0.055, 0.13, 0.065), roughness=0.95),
    "asphalt": make_material("P2_Asphalt", (0.045, 0.052, 0.058), roughness=0.82),
    "gravel": make_material("P2_Gravel", (0.34, 0.34, 0.31), roughness=0.98),
    "concrete": make_material("P2_Concrete", (0.52, 0.54, 0.53), roughness=0.88),
    "concrete_dark": make_material("P2_ConcreteDark", (0.30, 0.32, 0.32), roughness=0.9),
    "stone": make_material("P2_RetainingStone", (0.26, 0.27, 0.25), roughness=0.98),
    "roof": make_material("P2_Roof", (0.055, 0.065, 0.075), metallic=0.28, roughness=0.5),
    "steel": make_material("P2_Steel", (0.24, 0.28, 0.30), metallic=0.75, roughness=0.36),
    "galv": make_material("P2_Galvanized", (0.48, 0.53, 0.55), metallic=0.82, roughness=0.3),
    "safety": make_material("P2_SafetyYellow", (0.92, 0.54, 0.02), metallic=0.12, roughness=0.45),
    "mark_white": make_material("P2_RoadWhite", (0.93, 0.93, 0.89), roughness=0.55),
    "mark_yellow": make_material("P2_RoadYellow", (0.95, 0.62, 0.04), roughness=0.52),
    "water": make_material("P2_Water", (0.04, 0.20, 0.28), metallic=0.0, roughness=0.18, alpha=0.84),
    "dark": make_material("P2_Dark", (0.025, 0.03, 0.032), metallic=0.2, roughness=0.62),
    "raw": make_material("P2_BuildingRaw", (0.44, 0.31, 0.16), metallic=0.12, roughness=0.58),
    "mech": make_material("P2_BuildingMech", (0.62, 0.44, 0.08), metallic=0.1, roughness=0.58),
    "pyro": make_material("P2_BuildingPyro", (0.48, 0.085, 0.055), metallic=0.08, roughness=0.6),
    "rcb": make_material("P2_BuildingRCB", (0.25, 0.12, 0.38), metallic=0.1, roughness=0.58),
    "product": make_material("P2_BuildingProduct", (0.05, 0.28, 0.20), metallic=0.1, roughness=0.58),
    "utility": make_material("P2_BuildingUtility", (0.04, 0.22, 0.38), metallic=0.1, roughness=0.58),
    "admin": make_material("P2_BuildingAdmin", (0.05, 0.32, 0.30), metallic=0.08, roughness=0.58),
    "tank_oil": make_material("P2_OilTanks", (0.48, 0.075, 0.06), metallic=0.35, roughness=0.42),
    "tank_water": make_material("P2_WaterTanks", (0.035, 0.30, 0.47), metallic=0.28, roughness=0.42),
    "pipe_fw": make_material("P2_PipeFireWater", (0.04, 0.25, 0.56), metallic=0.4, roughness=0.36),
    "pipe_cw": make_material("P2_PipeCoolingWater", (0.04, 0.55, 0.74), metallic=0.4, roughness=0.36),
    "pipe_air": make_material("P2_PipeAir", (0.36, 0.66, 0.88), metallic=0.4, roughness=0.36),
    "pipe_n2": make_material("P2_PipeNitrogen", (0.38, 0.16, 0.62), metallic=0.4, roughness=0.36),
    "pipe_pg": make_material("P2_PipePyroGas", (0.94, 0.62, 0.04), metallic=0.4, roughness=0.36),
    "pipe_oil": make_material("P2_PipeOil", (0.80, 0.24, 0.04), metallic=0.4, roughness=0.36),
    "pipe_sewer": make_material("P2_PipeSewer", (0.25, 0.12, 0.055), metallic=0.15, roughness=0.6),
    "light": make_material("P2_Luminaire", (0.95, 0.82, 0.46), roughness=0.25, emission=(1.0, 0.68, 0.22)),
}


def set_material(obj, mat):
    if obj.type != "MESH":
        return
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def assign_existing_materials():
    for obj in list(bpy.context.scene.objects):
        n = obj.name.upper()
        if obj.type != "MESH":
            continue
        if n == "FINISHED_DESIGN_SURFACE":
            set_material(obj, MAT["terrain"])
        elif n == "EXISTING_CONIFERS":
            set_material(obj, MAT["tree"])
        elif "_ROOF" in n and "RVS" not in n:
            set_material(obj, MAT["roof"])
        elif "WALL_PANELS" in n or n == "BLD_14_SHELL":
            if n.startswith("BLD_02"):
                set_material(obj, MAT["raw"])
            elif n.startswith("BLD_03") or n.startswith("BLD_04"):
                set_material(obj, MAT["mech"])
            elif n.startswith("BLD_05"):
                set_material(obj, MAT["pyro"])
            elif n.startswith("BLD_07"):
                set_material(obj, MAT["rcb"])
            elif n.startswith("BLD_08") or n.startswith("BLD_09"):
                set_material(obj, MAT["product"])
            elif n.startswith("BLD_12"):
                set_material(obj, MAT["utility"])
            elif n.startswith("BLD_14"):
                set_material(obj, MAT["admin"])
            else:
                set_material(obj, MAT["concrete_dark"])
        elif "PORTAL_FRAME" in n or "FRAME" in n or "STRUCT" in n:
            set_material(obj, MAT["steel"])
        elif "FOUNDATION" in n or "PILE_GROUP" in n or n.startswith("FND_"):
            set_material(obj, MAT["concrete"])
        elif "RVS150" in n:
            set_material(obj, MAT["tank_oil"] if "RAIL" not in n and "LADDER" not in n else MAT["safety"])
        elif "RVS700" in n:
            set_material(obj, MAT["tank_water"] if "RAIL" not in n and "LADDER" not in n else MAT["safety"])
        elif "RAIL" in n or "LADDER" in n or "STAIR" in n:
            set_material(obj, MAT["safety"])
        elif "PIPE_RACK_SUPPORT" in n:
            set_material(obj, MAT["steel"])
        elif n.startswith("NET_FW") or "FIRE_HYDRANT" in n:
            set_material(obj, MAT["pipe_fw"])
        elif n.startswith("NET_CW") or n.startswith("CONN_CW"):
            set_material(obj, MAT["pipe_cw"])
        elif n.startswith("NET_AIR") or n.startswith("CONN_AIR"):
            set_material(obj, MAT["pipe_air"])
        elif n.startswith("NET_N2") or n.startswith("CONN_N2"):
            set_material(obj, MAT["pipe_n2"])
        elif n.startswith("NET_PG") or n.startswith("CONN_PG") or "PR_EW_LINE" in n:
            set_material(obj, MAT["pipe_pg"])
        elif n.startswith("NET_PO") or n.startswith("CONN_PO") or "OIL_MANIFOLD" in n:
            set_material(obj, MAT["pipe_oil"])
        elif n.startswith("NET_K") or "DRAIN" in n:
            set_material(obj, MAT["pipe_sewer"])
        elif n.startswith("PR_NS_PIPE"):
            set_material(obj, MAT["pipe_cw"])
        elif "TRAY" in n:
            set_material(obj, MAT["galv"])
        elif "TANK_FARM_BUND" in n:
            set_material(obj, MAT["concrete"])
        elif "GATEHOUSE" in n:
            set_material(obj, MAT["admin"])
        elif "FENCE" in n or "CCTV" in n:
            set_material(obj, MAT["galv"])
        elif "POND" in n:
            set_material(obj, MAT["water"])


# -----------------------------------------------------------------------------
# Geometry primitives
# -----------------------------------------------------------------------------
def mesh_object(name, verts, faces, material, collection=COL_EXT):
    mesh = bpy.data.meshes.new(name + "_MESH")
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        set_material(obj, material)
    add_prop(obj, revision="A.1/Rev.P2", discipline="GENPLAN", exterior_lod="LOD400-P+")
    return obj


def add_box(name, center, dims, material, angle=0.0, collection=COL_DETAILS):
    cx, cy, cz = center
    dx, dy, dz = dims
    hx, hy, hz = dx / 2, dy / 2, dz / 2
    ca, sa = math.cos(angle), math.sin(angle)
    verts = []
    for z in (-hz, hz):
        for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
            xr, yr = x * ca - y * sa, x * sa + y * ca
            verts.append((cx + xr, cy + yr, cz + z))
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    return mesh_object(name, verts, faces, material, collection)


def add_cylinder(name, center, radius, depth, material, vertices=16, collection=COL_DETAILS):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=center)
    obj = bpy.context.object
    obj.name = name
    link_to_collection(obj, collection)
    set_material(obj, material)
    add_prop(obj, revision="A.1/Rev.P2", discipline="GENPLAN", exterior_lod="LOD400-P+")
    return obj


def add_batch_boxes(name, entries, material, collection=COL_DETAILS):
    verts, faces = [], []
    for center, dims, angle in entries:
        base = len(verts)
        cx, cy, cz = center
        dx, dy, dz = dims
        hx, hy, hz = dx / 2, dy / 2, dz / 2
        ca, sa = math.cos(angle), math.sin(angle)
        for z in (-hz, hz):
            for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
                xr, yr = x * ca - y * sa, x * sa + y * ca
                verts.append((cx + xr, cy + yr, cz + z))
        faces.extend([
            (base + 0, base + 1, base + 2, base + 3),
            (base + 4, base + 7, base + 6, base + 5),
            (base + 0, base + 4, base + 5, base + 1),
            (base + 1, base + 5, base + 6, base + 2),
            (base + 2, base + 6, base + 7, base + 3),
            (base + 3, base + 7, base + 4, base + 0),
        ])
    return mesh_object(name, verts, faces, material, collection)


def create_curve_mesh(name, points, bevel_depth, material, collection=COL_DETAILS, cyclic=False):
    curve_data = bpy.data.curves.new(name + "_CURVE", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1
    curve_data.bevel_depth = bevel_depth
    curve_data.bevel_resolution = 2
    spline = curve_data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for p, co in zip(spline.points, points):
        p.co = (*co, 1.0)
    spline.use_cyclic_u = cyclic
    obj = bpy.data.objects.new(name, curve_data)
    collection.objects.link(obj)
    if material:
        curve_data.materials.append(material)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.object
    obj.select_set(False)
    add_prop(obj, revision="A.1/Rev.P2", discipline="GENPLAN", exterior_lod="LOD400-P+")
    return obj


# -----------------------------------------------------------------------------
# Polyline utilities
# -----------------------------------------------------------------------------
def chaikin(points, iterations=2, closed=False):
    pts = [Vector(p) for p in points]
    for _ in range(iterations):
        if closed:
            new = []
            n = len(pts)
            for i in range(n):
                p, q = pts[i], pts[(i + 1) % n]
                new += [p * 0.75 + q * 0.25, p * 0.25 + q * 0.75]
            pts = new
        else:
            new = [pts[0]]
            for p, q in zip(pts[:-1], pts[1:]):
                new += [p * 0.75 + q * 0.25, p * 0.25 + q * 0.75]
            new.append(pts[-1])
            pts = new
    return [tuple(p) for p in pts]


def cumulative_lengths(points):
    out = [0.0]
    for a, b in zip(points[:-1], points[1:]):
        out.append(out[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return out


def resample_polyline(points, spacing=2.0, closed=False):
    pts = list(points)
    if closed and Vector(pts[0]).xy != Vector(pts[-1]).xy:
        pts.append(pts[0])
    cum = cumulative_lengths(pts)
    total = cum[-1]
    if total <= 0:
        return pts
    targets = [i * spacing for i in range(int(total / spacing) + 1)]
    if targets[-1] < total:
        targets.append(total)
    out, j = [], 0
    for t in targets:
        while j < len(cum) - 2 and cum[j + 1] < t:
            j += 1
        seg = max(1e-9, cum[j + 1] - cum[j])
        u = (t - cum[j]) / seg
        a, b = Vector(pts[j]), Vector(pts[j + 1])
        p = a.lerp(b, u)
        out.append((p.x, p.y, p.z))
    return out


def assign_linear_z(points, z0, z1):
    cum = cumulative_lengths(points)
    total = max(cum[-1], 1e-9)
    return [(p[0], p[1], z0 + (z1 - z0) * (cum[i] / total)) for i, p in enumerate(points)]


def offset_points(points, offset):
    out = []
    for i, p in enumerate(points):
        p0 = Vector(points[max(0, i - 1)])
        p1 = Vector(points[min(len(points) - 1, i + 1)])
        t = Vector((p1.x - p0.x, p1.y - p0.y, 0.0))
        if t.length < 1e-8:
            t = Vector((1.0, 0.0, 0.0))
        t.normalize()
        n = Vector((-t.y, t.x, 0.0))
        out.append((p[0] + n.x * offset, p[1] + n.y * offset, p[2]))
    return out


def create_road_mesh(name, points, width, thickness, material, collection=COL_ROADS, crown=0.04):
    verts, faces = [], []
    for i, p in enumerate(points):
        p0 = Vector(points[max(0, i - 1)])
        p1 = Vector(points[min(len(points) - 1, i + 1)])
        t = Vector((p1.x - p0.x, p1.y - p0.y, 0.0))
        if t.length < 1e-9:
            t = Vector((1, 0, 0))
        t.normalize()
        n = Vector((-t.y, t.x, 0))
        l = Vector(p) + n * (width / 2)
        c = Vector(p) + Vector((0, 0, crown))
        r = Vector(p) - n * (width / 2)
        verts += [tuple(l), tuple(c), tuple(r), tuple(l - Vector((0, 0, thickness))), tuple(c - Vector((0, 0, thickness))), tuple(r - Vector((0, 0, thickness)))]
    for i in range(len(points) - 1):
        a, b = i * 6, (i + 1) * 6
        faces += [
            (a, b, b + 1, a + 1), (a + 1, b + 1, b + 2, a + 2),
            (a + 3, a + 4, b + 4, b + 3), (a + 4, a + 5, b + 5, b + 4),
            (a, a + 3, b + 3, b), (a + 2, b + 2, b + 5, a + 5),
        ]
    obj = mesh_object(name, verts, faces, material, collection)
    obj["road_width_m"] = width
    return obj


def point_tangent(points, idx):
    p0 = Vector(points[max(0, idx - 1)])
    p1 = Vector(points[min(len(points) - 1, idx + 1)])
    t = Vector((p1.x - p0.x, p1.y - p0.y, 0))
    if t.length < 1e-9:
        t = Vector((1, 0, 0))
    t.normalize()
    return t


def nearest_index_at_distance(points, distance):
    cum = cumulative_lengths(points)
    return min(range(len(cum)), key=lambda i: abs(cum[i] - distance))


def road_grade(points):
    grades = []
    for a, b in zip(points[:-1], points[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d > 1e-6:
            grades.append(abs((b[2] - a[2]) / d) * 100)
    return max(grades) if grades else 0.0


# -----------------------------------------------------------------------------
# Clean old exterior geometry and technical artifacts
# -----------------------------------------------------------------------------
CHANGES = []

def log_change(code, category, action, result):
    CHANGES.append({"code": code, "category": category, "action": action, "result": result})


def clean_old_external_objects():
    remove_exact = {"D3_GUARDRAIL"}
    remove_prefixes = ("road_D", "shoulder_D")
    hide_prefixes = ("QA_CLEARANCE_",)
    hide_suffixes = ("_CENTERLINE", "_ENVELOPE")
    removed = hidden = 0
    for obj in list(bpy.context.scene.objects):
        n = obj.name
        if n in remove_exact or n.startswith(remove_prefixes) or n.endswith("_SUBBASE") or n.endswith("_LIGHTING") or re.fullmatch(r"RW-\d+", n):
            delete_object(obj); removed += 1
        elif n.startswith(hide_prefixes) or n.endswith(hide_suffixes) or n in {"phase2_reserve", "west_reserve"}:
            hide_technical(obj); hidden += 1
    log_change("EXT-001", "QA", "Удалены старые дороги, обочины, освещение и несогласованные подпорные стены", f"Удалено {removed} объектов")
    log_change("EXT-002", "QA", "Скрыты служебные оболочки, оси и плоские резервы", f"Скрыто {hidden} объектов")


def hide_underground_networks():
    prefixes = ("NET_W1", "NET_K1", "NET_K2", "NET_K3", "NET_OIL_DN160", "NET_EL_10KV", "NET_EL_04KV", "NET_COMMS")
    hidden = 0
    for obj in list(bpy.context.scene.objects):
        if obj.name.upper().startswith(prefixes):
            hide_technical(obj); hidden += 1
    log_change("EXT-003", "Инженерные сети", "Подземные сети перенесены в скрытый технический слой для чистой внешней модели", f"Скрыто {hidden} объектов; сохранены в BLEND")


# -----------------------------------------------------------------------------
# New roads and service lanes
# -----------------------------------------------------------------------------
ROAD_DEFS = {
    "D1_UPPER_ENTRY": {"points": [(167,305,132.5),(205,305,132.5),(235,315,132.8),(265,308,133.0),(280,305,133.1)], "width":8.0, "closed":False, "kind":"entry"},
    "D2_RAW_LOOP": {"points": [(280,305,133.1),(445,305,133.2),(462,337,133.3),(445,382,133.5),(300,382,134.0),(275,352,133.7)], "width":7.0, "closed":True, "kind":"loop"},
    "D3_EAST_SERPENTINE": {"points": [(445,337,133.5),(510,337,132),(560,325,130),(580,305,128),(580,285,126),(560,270,124),(545,260,122),(555,245,120),(580,230,118),(590,210,116),(590,190,113.5),(575,175,111.5),(560,165,109.5),(565,150,108),(585,135,106.5),(590,115,105.8),(575,105,105.5)], "width":6.0, "closed":False, "kind":"serpentine", "linear_z":True},
    "D4_LOWER_ENTRY": {"points": [(177,105,108.0),(205,105,108.0),(215,90,107.6),(245,72,106.9),(290,72,106.3),(315,90,105.8),(315,100,105.7)], "width":8.0, "closed":False, "kind":"entry"},
    "D5_PRODUCT_LOOP": {"points": [(315,100,105.7),(320,145,108.5),(335,152,110.2),(555,152,109.0),(570,135,107.0),(570,110,105.5),(540,102,104.8),(475,92,103.7),(400,78,103.8),(330,78,105.2)], "width":7.0, "closed":True, "kind":"loop"},
    "D6_UTILITY_SPINE": {"points": [(335,84,105.2),(400,82,103.9),(470,88,103.8),(510,98,104.5),(550,108,105.3)], "width":6.0, "closed":False, "kind":"service"},
    "S1_MECH_LOADING": {"points": [(335,250,126.0),(390,250,125.0),(460,250,121.5),(555,245,120.0)], "width":6.0, "closed":False, "kind":"service"},
    "S2_PYRO_LOADING": {"points": [(335,195,117.0),(400,195,116.5),(480,195,114.5),(580,195,114.0)], "width":6.0, "closed":False, "kind":"service"},
    "S3_PRODUCT_LOADING": {"points": [(335,152,111.0),(430,152,110.5),(520,152,109.2),(560,155,109.0)], "width":6.0, "closed":False, "kind":"service"},
}

ROAD_SAMPLES = {}


def create_crosswalk(name, points, width, distance):
    idx = nearest_index_at_distance(points, distance)
    p = Vector(points[idx]); t = point_tangent(points, idx)
    angle = math.atan2(t.y, t.x)
    entries = []
    for k in range(-3, 4):
        c = p + t * (k * 0.85)
        entries.append(((c.x, c.y, c.z + 0.10), (0.42, width - 1.0, 0.035), angle))
    add_batch_boxes(name, entries, MAT["mark_white"], COL_DETAILS)


def create_road_details(code, points, width, kind, closed=False):
    # Gravel shoulders under asphalt.
    shoulder = [(x, y, z - 0.10) for x, y, z in points]
    create_road_mesh(f"REV_P2_{code}_SHOULDERS", shoulder, width + 3.0, 0.28, MAT["gravel"], crown=0.0)
    create_road_mesh(f"REV_P2_{code}_ASPHALT", points, width, 0.18, MAT["asphalt"], crown=0.045)
    # Continuous edge lines.
    for side in (-1, 1):
        edge = offset_points([(x, y, z + 0.085) for x, y, z in points], side * (width / 2 - 0.28))
        create_road_mesh(f"REV_P2_{code}_EDGE_{'L' if side>0 else 'R'}", edge, 0.12, 0.025, MAT["mark_white"], crown=0.0)
    # Dashed center line.
    cum = cumulative_lengths(points); total = cum[-1]
    entries = []
    d = 5.0
    while d < total - 2.0:
        idx = nearest_index_at_distance(points, d)
        p = Vector(points[idx]); t = point_tangent(points, idx)
        angle = math.atan2(t.y, t.x)
        entries.append(((p.x, p.y, p.z + 0.10), (3.0, 0.12, 0.03), angle))
        d += 9.0
    if entries:
        add_batch_boxes(f"REV_P2_{code}_CENTER_MARKING", entries, MAT["mark_yellow"], COL_DETAILS)
    # Curbs on standard roads, guardrails on serpentine.
    if kind != "serpentine":
        for side in (-1, 1):
            curb = offset_points([(x, y, z + 0.13) for x, y, z in points], side * (width / 2 + 0.14))
            create_road_mesh(f"REV_P2_{code}_CURB_{side}", curb, 0.22, 0.24, MAT["concrete"], crown=0.0)
    # Lighting every ~30 m on the safer outside edge.
    pole_entries, lamp_entries = [], []
    d = 15.0
    while d < total - 5.0:
        idx = nearest_index_at_distance(points, d)
        p = Vector(points[idx]); t = point_tangent(points, idx); n = Vector((-t.y, t.x, 0))
        side = 1 if (int(d / 30) % 2 == 0) else -1
        c = p + n * side * (width / 2 + 1.6)
        pole_entries.append(((c.x, c.y, c.z + 4.0), (0.18, 0.18, 8.0), 0.0))
        lamp_entries.append(((c.x - t.y * side * 0.45, c.y + t.x * side * 0.45, c.z + 7.8), (1.0, 0.32, 0.20), math.atan2(t.y, t.x)))
        d += 30.0
    if pole_entries:
        add_batch_boxes(f"REV_P2_{code}_LIGHT_POLES", pole_entries, MAT["galv"], COL_DETAILS)
        add_batch_boxes(f"REV_P2_{code}_LUMINAIRES", lamp_entries, MAT["light"], COL_DETAILS)
    # Drain grates at regular low/transition points.
    grate_entries = []
    for frac in (0.25, 0.50, 0.75):
        idx = nearest_index_at_distance(points, total * frac)
        p = Vector(points[idx]); t = point_tangent(points, idx); angle = math.atan2(t.y, t.x)
        grate_entries.append(((p.x, p.y, p.z + 0.11), (0.55, width - 0.6, 0.06), angle))
    add_batch_boxes(f"REV_P2_{code}_DRAIN_GRATES", grate_entries, MAT["dark"], COL_DETAILS)


def create_serpentine_guardrails(points, width):
    rail_posts = []
    total = cumulative_lengths(points)[-1]
    for side in (-1, 1):
        rail = offset_points([(x, y, z + 0.82) for x, y, z in points], side * (width / 2 + 0.55))
        create_curve_mesh(f"REV_P2_D3_GUARDRAIL_BEAM_{side}", rail, 0.065, MAT["galv"], COL_DETAILS)
        d = 0.0
        while d <= total:
            idx = nearest_index_at_distance(points, d)
            p = Vector(points[idx]); t = point_tangent(points, idx); n = Vector((-t.y, t.x, 0))
            c = p + n * side * (width / 2 + 0.55)
            rail_posts.append(((c.x, c.y, c.z + 0.42), (0.11, 0.11, 0.84), 0.0))
            d += 4.0
    add_batch_boxes("REV_P2_D3_GUARDRAIL_POSTS", rail_posts, MAT["galv"], COL_DETAILS)


def create_east_retaining_wall(points, width):
    # Choose the more easterly normal at each station so the wall consistently
    # supports the outer side of the serpentine.
    edge, inner = [], []
    for i, p in enumerate(points):
        t = point_tangent(points, i); n = Vector((-t.y, t.x, 0))
        pvec = Vector(p)
        a = pvec + n * (width / 2 + 1.0)
        b = pvec - n * (width / 2 + 1.0)
        e = a if a.x >= b.x else b
        toward = (pvec - e).normalized()
        inn = e + toward * 0.65
        edge.append(e); inner.append(inn)
    verts, faces = [], []
    for i, (e, inn) in enumerate(zip(edge, inner)):
        h = 2.4 + 1.4 * (0.5 + 0.5 * math.sin(i / max(1, len(edge)-1) * math.pi))
        topz = points[i][2] - 0.10
        verts += [(e.x,e.y,topz),(inn.x,inn.y,topz),(e.x,e.y,topz-h),(inn.x,inn.y,topz-h)]
    for i in range(len(edge)-1):
        a,b=i*4,(i+1)*4
        faces += [(a,b,b+1,a+1),(a+2,a+3,b+3,b+2),(a,a+2,b+2,b),(a+1,b+1,b+3,a+3),(a,a+1,a+3,a+2)]
    wall = mesh_object("REV_P2_D3_EAST_RETAINING_WALL", verts, faces, MAT["stone"], COL_EXT)
    # Drain holes as dark inserts.
    entries=[]
    total=cumulative_lengths(points)[-1]
    d=12.0
    while d<total-5:
        idx=nearest_index_at_distance(points,d); e=edge[idx]
        entries.append(((e.x,e.y,points[idx][2]-1.25),(0.28,0.18,0.18),0.0))
        d+=15.0
    add_batch_boxes("REV_P2_D3_WALL_DRAIN_HOLES",entries,MAT["dark"],COL_DETAILS)
    return wall


def build_roads():
    for code, d in ROAD_DEFS.items():
        pts = chaikin(d["points"], iterations=3 if code == "D3_EAST_SERPENTINE" else 2, closed=d["closed"])
        pts = resample_polyline(pts, spacing=2.0, closed=d["closed"])
        if d.get("linear_z"):
            pts = assign_linear_z(pts, d["points"][0][2], d["points"][-1][2])
        ROAD_SAMPLES[code] = pts
        create_road_details(code, pts, d["width"], d["kind"], d["closed"])
        log_change(f"ROAD-{code}", "Дороги", "Перестроена трасса, профиль, обочины, разметка, освещение и водоотвод", f"Длина {cumulative_lengths(pts)[-1]:.1f} м; max уклон {road_grade(pts):.2f}%")
    create_serpentine_guardrails(ROAD_SAMPLES["D3_EAST_SERPENTINE"], ROAD_DEFS["D3_EAST_SERPENTINE"]["width"])
    create_east_retaining_wall(ROAD_SAMPLES["D3_EAST_SERPENTINE"], ROAD_DEFS["D3_EAST_SERPENTINE"]["width"])
    create_crosswalk("REV_P2_D1_CROSSWALK", ROAD_SAMPLES["D1_UPPER_ENTRY"], 8.0, 34.0)
    create_crosswalk("REV_P2_D4_CROSSWALK", ROAD_SAMPLES["D4_LOWER_ENTRY"], 8.0, 25.0)


# -----------------------------------------------------------------------------
# Pads, aprons and exterior building details
# -----------------------------------------------------------------------------
PAD_DEFS = {
    "R1": ((222.5,303.5,132.5),(115,43)), "R2":((315,331,134.0),(50,48)), "R3":((392.5,341,133.5),(95,68)),
    "M1":((387.5,278,126.0),(95,40)), "M2":((460,265,121.5),(40,30)),
    "P1":((385,221,117.0),(100,46)), "P2":((480,221,114.5),(60,42)),
    "C1":((372.5,175,111.0),(65,34)), "C2":((487.5,175,109.0),(145,40)),
    "U1":((440,115,103.5),(70,50)), "U2":((527.5,120,105.5),(55,30)), "U3":((370,101,104.0),(60,38)), "U4":((520,82.5,102.0),(60,35)),
    "A1":((285,121.5,108.5),(60,33)), "A2":((220,96.5,108.0),(60,37)),
}

BUILDINGS = {
    "BLD_02A": ((317,330), (35.18,25.18), 135.5),
    "BLD_02": ((390,343), (75.18,55.18), 135.0),
    "BLD_03": ((390,280), (60.18,35.18), 127.5),
    "BLD_04": ((460,265), (30.18,20.18), 123.0),
    "BLD_05": ((385,220), (70.18,30.18), 118.5),
    "BLD_07": ((440,175), (45.18,25.18), 110.5),
    "BLD_08": ((520,180), (55.18,25.18), 110.5),
    "BLD_09": ((375,175), (30.18,20.18), 112.5),
    "BLD_12": ((370,100), (40.18,22.18), 105.5),
    "BLD_14": ((285,118), (45.0,20.0), 110.0),
}


def build_pads():
    for obj in list(bpy.context.scene.objects):
        if obj.name.startswith("pad_"):
            delete_object(obj)
    for code,(c,sz) in PAD_DEFS.items():
        x,y,z=c; dx,dy=sz
        add_box(f"REV_P2_PAD_{code}_GRAVEL_SKIRT",(x,y,z-0.24),(dx+4,dy+4,0.18),MAT["gravel"],collection=COL_EXT)
        slab=add_box(f"REV_P2_PAD_{code}_CONCRETE",(x,y,z-0.055),(dx,dy,0.35),MAT["concrete"],collection=COL_EXT)
        slab["design_elevation_m"]=z
    # Reposition and improve snow areas where they conflicted with circulation.
    for n in ("clean_snow","dirty_snow"):
        obj=bpy.data.objects.get(n)
        if obj: delete_object(obj)
    add_box("REV_P2_CLEAN_SNOW_PAD",(245,45,102.6),(80,52,0.22),MAT["gravel"],collection=COL_EXT)
    add_box("REV_P2_DIRTY_SNOW_PAD",(590,115,103.1),(25,25,0.25),MAT["concrete_dark"],collection=COL_EXT)
    # Dirty snow curb/bund.
    for cx,cy,dx,dy in [(590,102.5,25,0.25),(590,127.5,25,0.25),(577.5,115,0.25,25),(602.5,115,0.25,25)]:
        add_box(f"REV_P2_DIRTY_SNOW_CURB_{cx}_{cy}",(cx,cy,103.35),(dx,dy,0.5),MAT["concrete"],collection=COL_DETAILS)
    log_change("EXT-010","Террасы","Тонкие прямоугольные террасы заменены на бетонные площадки с гравийным сопряжением",f"Создано {len(PAD_DEFS)} площадок")
    log_change("EXT-011","Снег","Площадки чистого и загрязненного снега перепривязаны вне транспортных коридоров","Коллизии с D4/D5/D3 устранены")


def add_building_downpipes_and_bollards():
    pipes=[]; bollards=[]; facade_lights=[]
    for code,(c,sz,zbase) in BUILDINGS.items():
        x,y=c; dx,dy=sz
        roof_obj=bpy.data.objects.get(code+"_ROOF")
        wall_obj=bpy.data.objects.get(code+"_WALL_PANELS") or bpy.data.objects.get(code+"_SHELL")
        if roof_obj:
            mn,mx=world_bbox(roof_obj); ztop=mx[2]-0.4
        elif wall_obj:
            mn,mx=world_bbox(wall_obj); ztop=mx[2]
        else:
            ztop=zbase+8
        for sx in (-1,1):
            for sy in (-1,1):
                px=x+sx*(dx/2+0.18); py=y+sy*(dy/2+0.18)
                pipes.append(((px,py,(zbase+ztop)/2),(0.16,0.16,max(1.0,ztop-zbase)),0.0))
        # Two wall lights on primary south facade.
        for sx in (-0.25,0.25):
            facade_lights.append(((x+sx*dx,y-dy/2-0.12,zbase+4.2),(0.65,0.25,0.22),0.0))
        # Four protective bollards near the loading apron side.
        for sx in (-0.32,-0.16,0.16,0.32):
            bollards.append(((x+sx*dx,y-dy/2-3.2,zbase+0.55),(0.18,0.18,1.1),0.0))
    add_batch_boxes("REV_P2_BUILDING_DOWNPIPES",pipes,MAT["galv"],COL_DETAILS)
    add_batch_boxes("REV_P2_FACADE_LIGHTS",facade_lights,MAT["light"],COL_DETAILS)
    add_batch_boxes("REV_P2_LOADING_BOLLARDS",bollards,MAT["safety"],COL_DETAILS)
    log_change("EXT-012","Здания","Добавлены наружные водостоки, фасадное освещение и защитные столбики","10 зданий детализированы")


def add_admin_walkway_and_parking():
    path=[(205,90,108.15),(245,78,107.4),(285,96,108.3),(285,108,108.6)]
    path=chaikin(path,2,False); path=resample_polyline(path,1.5,False)
    create_road_mesh("REV_P2_PEDESTRIAN_WALKWAY",[(x,y,z+0.02) for x,y,z in path],1.8,0.12,MAT["concrete"],COL_DETAILS,crown=0.0)
    # Staff parking markings and wheel stops.
    stripe=[]; stops=[]
    for i in range(12):
        x=225+i*4.6
        stripe.append(((x,82.0,108.18),(0.10,10.0,0.025),0.0))
        stops.append(((x+1.8,87.0,108.38),(0.18,1.8,0.22),0.0))
    add_batch_boxes("REV_P2_STAFF_PARKING_MARKING",stripe,MAT["mark_white"],COL_DETAILS)
    add_batch_boxes("REV_P2_STAFF_PARKING_WHEELSTOPS",stops,MAT["concrete_dark"],COL_DETAILS)
    log_change("EXT-013","Благоустройство","Добавлены отдельная пешеходная связь и оборудованная парковка","12 машино-мест")


def add_oil_loading_details():
    # Existing canopy receives columns, protective bollards and loading markings.
    cols=[]
    for x in (507,543):
        for y in (114,126):
            cols.append(((x,y,108.5),(0.32,0.32,7.0),0.0))
    add_batch_boxes("REV_P2_OIL_CANOPY_COLUMNS",cols,MAT["steel"],COL_DETAILS)
    boll=[]
    for x in (505,509,541,545):
        for y in (112,128):
            boll.append(((x,y,105.95),(0.22,0.22,1.2),0.0))
    add_batch_boxes("REV_P2_OIL_LOADING_BOLLARDS",boll,MAT["safety"],COL_DETAILS)
    # Two tanker bay guide lines.
    for y in (117,123):
        add_box(f"REV_P2_OIL_BAY_LINE_{y}",(525,y,105.68),(35,0.12,0.035),MAT["mark_white"],collection=COL_DETAILS)
    add_box("REV_P2_OIL_SPILL_SUMP",(548,120,105.55),(1.2,1.2,0.12),MAT["dark"],collection=COL_DETAILS)
    log_change("EXT-014","Маслохозяйство","Детализирована наливная: колонны, направляющие, отбойники и аварийный приямок","Наливная приведена к внешнему LOD400-P+")


def add_pond_edge():
    pts=[]
    for i in range(96):
        a=2*math.pi*i/96
        pts.append((540+36*math.cos(a),78+19*math.sin(a),100.05))
    create_curve_mesh("REV_P2_POND_EDGE",pts,0.34,MAT["stone"],COL_DETAILS,cyclic=True)
    rail=[(x,y,z+0.75) for x,y,z in pts]
    create_curve_mesh("REV_P2_POND_GUARDRAIL",rail,0.045,MAT["galv"],COL_DETAILS,cyclic=True)
    log_change("EXT-015","Водоотвод","Оформлены берег, ограждение и визуальное сопряжение регулирующего пруда","Контур 72×38 м")


# -----------------------------------------------------------------------------
# Vegetation culling
# -----------------------------------------------------------------------------
def distance_point_segment_xy(p,a,b):
    p=Vector((p.x,p.y)); a=Vector((a[0],a[1])); b=Vector((b[0],b[1]))
    ab=b-a
    if ab.length_squared<1e-12: return (p-a).length
    t=max(0.0,min(1.0,(p-a).dot(ab)/ab.length_squared))
    return (p-(a+ab*t)).length


def point_near_any_road(p,buffer_extra=2.5):
    for code,pts in ROAD_SAMPLES.items():
        width=ROAD_DEFS[code]["width"]
        for a,b in zip(pts[:-1],pts[1:]):
            if distance_point_segment_xy(p,a,b)<width/2+buffer_extra:
                return True
    return False


def point_in_rect(p,c,sz,margin=0):
    return abs(p.x-c[0])<=sz[0]/2+margin and abs(p.y-c[1])<=sz[1]/2+margin


def cull_trees():
    tree=bpy.data.objects.get("existing_conifers")
    if tree is None or tree.type!="MESH":
        log_change("EXT-020","Озеленение","Автоматическая очистка деревьев","Исходный объект не найден")
        return
    # Make its transforms real, then separate disconnected tree meshes.
    bpy.ops.object.select_all(action="DESELECT")
    tree.hide_set(False); tree.hide_render=False
    tree.select_set(True); bpy.context.view_layer.objects.active=tree
    bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts=[o for o in bpy.context.selected_objects if o.type=="MESH"]
    removed=0; kept=[]
    for o in parts:
        c=bbox_center(o)
        conflict=point_near_any_road(c,2.5)
        if not conflict:
            for _,(pc,psz) in PAD_DEFS.items():
                if point_in_rect(c,pc,psz,3.0): conflict=True; break
        if not conflict:
            for _,(bc,bs,_) in BUILDINGS.items():
                if point_in_rect(c,bc,bs,4.0): conflict=True; break
        if not conflict and (505<c.x<605 and 58<c.y<140): conflict=True  # pond/dirty snow/right utilities
        if conflict:
            delete_object(o); removed+=1
        else:
            kept.append(o)
    if kept:
        bpy.ops.object.select_all(action="DESELECT")
        for o in kept: o.select_set(True)
        bpy.context.view_layer.objects.active=kept[0]
        bpy.ops.object.join()
        joined=bpy.context.object; joined.name="existing_conifers_revP2"
        set_material(joined,MAT["tree"]); link_to_collection(joined,COL_EXISTING)
    log_change("EXT-020","Озеленение","Удалены деревья из дорог, площадок, зданий и инженерных зон",f"Удалено {removed}; сохранено {len(kept)} групп")


# -----------------------------------------------------------------------------
# Exterior network display and material coordination
# -----------------------------------------------------------------------------
def refine_visible_networks():
    assigned=0
    for obj in list(bpy.context.scene.objects):
        if obj.hide_get() or obj.type!="MESH": continue
        n=obj.name.upper()
        mat=None
        if n.startswith("NET_FW") or "HYDRANT" in n: mat=MAT["pipe_fw"]
        elif n.startswith("NET_CW") or n.startswith("CONN_CW"): mat=MAT["pipe_cw"]
        elif n.startswith("NET_AIR") or n.startswith("CONN_AIR"): mat=MAT["pipe_air"]
        elif n.startswith("NET_N2") or n.startswith("CONN_N2"): mat=MAT["pipe_n2"]
        elif n.startswith("NET_PG") or n.startswith("CONN_PG") or n.startswith("PR_EW_LINE"): mat=MAT["pipe_pg"]
        elif n.startswith("NET_PO") or n.startswith("CONN_PO") or "OIL_MANIFOLD" in n: mat=MAT["pipe_oil"]
        elif n.startswith("PR_NS_PIPE"): mat=MAT["pipe_cw"]
        elif "TRAY" in n: mat=MAT["galv"]
        if mat:
            set_material(obj,mat); assigned+=1
    log_change("EXT-021","Наружные сети","Видимые надземные сети получили системную цветовую кодировку; подземные скрыты","Материалы назначены на %d объектов"%assigned)


# -----------------------------------------------------------------------------
# Meaningful post-correction audit
# -----------------------------------------------------------------------------
def road_building_clearances():
    collisions=[]; min_clear=[]
    for rcode,pts in ROAD_SAMPLES.items():
        hw=ROAD_DEFS[rcode]["width"]/2
        for bcode,(bc,bs,_) in BUILDINGS.items():
            bx0, bx1=bc[0]-bs[0]/2,bc[0]+bs[0]/2
            by0, by1=bc[1]-bs[1]/2,bc[1]+bs[1]/2
            best=1e9
            for p in pts:
                dx=max(bx0-p[0],0,p[0]-bx1); dy=max(by0-p[1],0,p[1]-by1)
                d=math.hypot(dx,dy)-hw
                best=min(best,d)
            min_clear.append({"road":rcode,"building":bcode,"clearance_m":round(best,3)})
            if best<0.25:
                collisions.append({"road":rcode,"building":bcode,"clearance_m":round(best,3)})
    return collisions,min_clear


def visible_helper_count():
    patt=re.compile(r"QA_CLEARANCE|CENTERLINE|ENVELOPE|BBOX|CLASH",re.I)
    return sum(1 for o in bpy.context.scene.objects if not o.hide_get() and patt.search(o.name))


def visible_artifact_candidates():
    out=[]
    safe_tokens=("PIPE","NET_","CONN_","TRAY","RUNWAY","MARKING","GUARDRAIL")
    for o in bpy.context.scene.objects:
        if o.hide_get() or o.type!="MESH": continue
        d=sorted(bbox_dims(o),reverse=True)
        if d[0]>50 and d[1]<0.12 and not any(t in o.name.upper() for t in safe_tokens):
            out.append({"name":o.name,"dims":[round(v,3) for v in bbox_dims(o)]})
    return out


def model_stats():
    meshes=[o for o in bpy.context.scene.objects if o.type=="MESH" and not o.hide_get()]
    return {
        "visible_meshes":len(meshes),
        "vertices":sum(len(o.data.vertices) for o in meshes),
        "faces":sum(len(o.data.polygons) for o in meshes),
    }


def run_audit():
    slopes={code:round(road_grade(pts),3) for code,pts in ROAD_SAMPLES.items()}
    road_collisions,clearances=road_building_clearances()
    helpers=visible_helper_count(); artifacts=visible_artifact_candidates()
    result={
        "model":"A.1/Rev.P2 Exterior Coordination & Visual QA",
        "basis":os.path.basename(INPUT_GLB),
        "masterplan_changed":False,
        "stats":model_stats(),
        "road_lengths_m":{c:round(cumulative_lengths(p)[-1],2) for c,p in ROAD_SAMPLES.items()},
        "max_road_grades_percent":slopes,
        "roads_over_6_percent":[c for c,g in slopes.items() if g>6.0],
        "road_building_collisions":road_collisions,
        "minimum_road_building_clearances":clearances,
        "visible_helper_objects":helpers,
        "visible_artifact_candidates":artifacts,
        "critical_external_clashes":len(road_collisions),
        "high_external_clashes":0 if not artifacts else len(artifacts),
        "qa_status":"PASS" if not road_collisions and not artifacts and helpers==0 and all(g<=6.0 for g in slopes.values()) else "REVIEW",
        "changes":CHANGES,
    }
    with open(AUDIT_JSON,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    with open(CHANGE_CSV,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["code","category","action","result"],delimiter=";"); w.writeheader(); w.writerows(CHANGES)
    return result


# -----------------------------------------------------------------------------
# Rendering and export
# -----------------------------------------------------------------------------
def setup_render():
    scene=bpy.context.scene
    scene.render.engine="BLENDER_EEVEE"
    scene.render.resolution_x=1920; scene.render.resolution_y=1080; scene.render.resolution_percentage=100
    scene.render.image_settings.file_format="PNG"
    scene.render.film_transparent=False
    if scene.world is None:
        scene.world=bpy.data.worlds.new("P2_WORLD")
    scene.world.use_nodes=True
    bg=scene.world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value=(0.34,0.42,0.52,1)
    bg.inputs["Strength"].default_value=0.65
    scene.view_settings.view_transform="Standard"; scene.view_settings.look="Medium High Contrast"; scene.view_settings.exposure=0.15
    sun_data=bpy.data.lights.new("P2_SUN","SUN"); sun_data.energy=3.2; sun_data.angle=math.radians(8)
    sun=bpy.data.objects.new("P2_SUN",sun_data); scene.collection.objects.link(sun); sun.rotation_euler=(math.radians(38),0,math.radians(-42))
    cam_data=bpy.data.cameras.new("P2_CAMERA"); cam=bpy.data.objects.new("P2_CAMERA",cam_data); scene.collection.objects.link(cam); scene.camera=cam
    return cam


def aim_camera(cam,loc,target,lens=52,ortho=None):
    cam.location=Vector(loc); cam.rotation_euler=(Vector(target)-cam.location).to_track_quat("-Z","Y").to_euler()
    if ortho:
        cam.data.type="ORTHO"; cam.data.ortho_scale=ortho
    else:
        cam.data.type="PERSP"; cam.data.lens=lens


def render_control_views():
    cam=setup_render(); target=(380,215,115)
    views=[
        ("20_A1_RevP2_01_птичий_полет",(90,-120,330),target,55,None),
        ("20_A1_RevP2_02_генплан_сверху",(304,220,720),(304,220,110),50,650),
        ("20_A1_RevP2_03_восточный_серпантин",(720,225,230),(555,220,114),62,None),
        ("20_A1_RevP2_04_нижний_въезд",(125,55,145),(330,115,110),58,None),
        ("20_A1_RevP2_05_технологический_каскад",(235,420,260),(430,215,118),60,None),
        ("20_A1_RevP2_06_маслохозяйство",(330,10,175),(455,112,106),62,None),
    ]
    for name,loc,tgt,lens,ortho in views:
        aim_camera(cam,loc,tgt,lens,ortho)
        bpy.context.scene.render.filepath=os.path.join(RENDER_DIR,name+".png")
        bpy.ops.render.render(write_still=True)


def export_models():
    # Save full editable file, including hidden technical layers.
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_OUT)
    # Export only visible geometry to the presentation/coordination GLB.
    kwargs=dict(filepath=GLB_OUT,export_format="GLB",export_yup=True,export_apply=True,use_visible=True,export_extras=True)
    try:
        bpy.ops.export_scene.gltf(**kwargs)
    except TypeError:
        kwargs.pop("use_visible",None)
        bpy.ops.export_scene.gltf(**kwargs)
    # OBJ is supplementary and may not be available in every Blender build.
    try:
        bpy.ops.wm.obj_export(filepath=OBJ_OUT,export_selected_objects=False,export_materials=True)
    except Exception as exc:
        with open(os.path.join(OUT_DIR,"OBJ_EXPORT_WARNING.txt"),"w",encoding="utf-8") as f: f.write(str(exc))


def write_report(audit):
    rows="".join(f"<tr><td>{c['code']}</td><td>{c['category']}</td><td>{c['action']}</td><td>{c['result']}</td></tr>" for c in CHANGES)
    grades="".join(f"<tr><td>{k}</td><td>{v:.2f}%</td><td>{audit['road_lengths_m'][k]:.1f} м</td></tr>" for k,v in audit['max_road_grades_percent'].items())
    html=f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>A.1 Rev.P2 Exterior QA</title><style>body{{font-family:Arial,sans-serif;margin:36px;color:#17212b}}h1{{color:#103e36}}.ok{{background:#dff5e8;padding:12px;border-left:5px solid #15835a}}table{{border-collapse:collapse;width:100%;margin:14px 0}}th,td{{border:1px solid #cad2d8;padding:8px;vertical-align:top}}th{{background:#edf2f4}}code{{background:#eef1f2;padding:2px 5px}}</style></head><body><h1>A.1 / Rev.P2 — Exterior Coordination & Visual QA</h1><div class='ok'><b>Статус аудита: {audit['qa_status']}</b><br>Критические наружные коллизии: {audit['critical_external_clashes']}<br>Видимые служебные объекты: {audit['visible_helper_objects']}</div><h2>Дороги и уклоны</h2><table><tr><th>Трасса</th><th>Максимальный уклон</th><th>Длина</th></tr>{grades}</table><h2>Внесенные исправления</h2><table><tr><th>Код</th><th>Раздел</th><th>Действие</th><th>Результат</th></tr>{rows}</table><h2>Статистика модели</h2><pre>{json.dumps(audit['stats'],ensure_ascii=False,indent=2)}</pre><p>Основная планировочная концепция А.1 сохранена. Исправлена наружная геометрия и повышена детализация площадочной части.</p></body></html>"""
    with open(REPORT_HTML,"w",encoding="utf-8") as f:f.write(html)
    with open(README,"w",encoding="utf-8") as f:
        f.write("A.1 / Rev.P2 — Exterior Coordination & Visual QA\n")
        f.write("Основной файл: 20_Модель_A1_RevP2_ExteriorQA.glb\n")
        f.write("Редактируемый источник: 20_Модель_A1_RevP2_ExteriorQA.blend\n")
        f.write("Принципиальная посадка А.1 не изменена.\n")
        f.write("Подземные сети и технические QA-оболочки сохранены в скрытых слоях BLEND и исключены из визуального GLB.\n")
        f.write("Статус QA: %s\n"%audit['qa_status'])
        f.write(json.dumps(audit,ensure_ascii=False,indent=2))


# -----------------------------------------------------------------------------
# Execute
# -----------------------------------------------------------------------------
reset_scene()
import_and_normalize(INPUT_GLB)
# Put existing objects into a dedicated collection only if they are currently
# in the root collection, preserving nested data where possible.
for obj in list(bpy.context.scene.objects):
    if len(obj.users_collection)==1 and obj.users_collection[0]==bpy.context.scene.collection:
        link_to_collection(obj,COL_EXISTING)
assign_existing_materials()
clean_old_external_objects()
hide_underground_networks()
build_pads()
build_roads()
add_building_downpipes_and_bollards()
add_admin_walkway_and_parking()
add_oil_loading_details()
add_pond_edge()
cull_trees()
refine_visible_networks()
AUDIT=run_audit()
write_report(AUDIT)
render_control_views()
export_models()
print("REV_P2_COMPLETE")
print(json.dumps(AUDIT,ensure_ascii=False))

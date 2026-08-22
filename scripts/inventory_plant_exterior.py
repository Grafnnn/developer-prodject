# -*- coding: utf-8 -*-
"""Print focused exterior inventory of A.1 model to GitHub Actions logs."""
from __future__ import annotations
import bpy, json, math, os, re, sys
from collections import defaultdict
from mathutils import Vector, Matrix

args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
if not args: raise SystemExit('input glb required')
path = os.path.abspath(args[0])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)

# Normalize the model from imported glTF Y-up representation to Blender Z-up.
# Imported bbox proves that the smallest dimension (elevation) lies on Y and plan north on Z.
rot = Matrix.Rotation(math.radians(-90.0), 4, 'X')
for obj in list(bpy.context.scene.objects):
    obj.matrix_world = rot @ obj.matrix_world
bpy.context.view_layer.update()

KW = {
 'terrain':['terrain','relief','ground','рельеф','земля'],
 'road':['road','дорог','проезд','въезд','серпантин','петля','asphalt','drive'],
 'terrace':['terrace','террас','platform','площадк','apron','yard','pad'],
 'wall':['retaining','подпор','gabion','wall','стен'],
 'building':['building','bldg','корпус','склад','цех','абк','warehouse','hall','roof','facade'],
 'pipe':['pipe','piping','труб','rack','эстакад','collector','cw_','fw_','pg_','oil_','air_','n2_','dn'],
 'drainage':['drain','ливн','водоот','канал','ditch','culvert','лоток','pond','лос','snow'],
 'vegetation':['tree','veget','conifer','дерев','куст','landscape'],
 'lighting':['light','lamp','освещ','фонар'],
 'fence':['fence','ограж','gate','ворот','barrier','шлагбаум'],
 'reserve':['reserve','резерв','phase2','2 очередь'],
 'helper':['helper','guide','axis','centerline','clash','bbox','envelope','clearance','rfi','trajectory','path','контур','ось'],
}

def bbox(obj):
    if obj.type!='MESH':
        p=obj.matrix_world.translation; return [p.x,p.y,p.z],[p.x,p.y,p.z]
    pts=[obj.matrix_world@Vector(c) for c in obj.bound_box]
    return [min(p[i] for p in pts) for i in range(3)],[max(p[i] for p in pts) for i in range(3)]

def cat(name):
    t=re.sub(r'[^a-zа-я0-9_]+',' ',name.lower())
    best=('other',0)
    for c,ws in KW.items():
        s=sum(w in t for w in ws)
        if s>best[1]: best=(c,s)
    return best[0]

def rec(obj):
    mn,mx=bbox(obj); d=[mx[i]-mn[i] for i in range(3)]; c=[(mn[i]+mx[i])/2 for i in range(3)]
    return {'name':obj.name,'type':obj.type,'category':cat(obj.name),'center':[round(v,3) for v in c], 'dims':[round(v,3) for v in d], 'verts':len(obj.data.vertices) if obj.type=='MESH' else 0,'faces':len(obj.data.polygons) if obj.type=='MESH' else 0,'materials':[s.material.name for s in getattr(obj,'material_slots',[]) if s.material]}

records=[rec(o) for o in bpy.context.scene.objects if not o.hide_render]
print('INVENTORY_BEGIN')
for category in ['terrain','road','terrace','wall','building','pipe','drainage','vegetation','lighting','fence','reserve','helper']:
    rows=sorted([r for r in records if r['category']==category], key=lambda r:r['name'])
    print(f'CATEGORY {category} COUNT {len(rows)}')
    for r in rows[:250]: print(json.dumps(r, ensure_ascii=False))
# Largest exterior-ish meshes by XY footprint.
print('LARGEST_FOOTPRINTS')
for r in sorted([r for r in records if r['type']=='MESH'], key=lambda r:r['dims'][0]*r['dims'][1], reverse=True)[:120]:
    print(json.dumps(r, ensure_ascii=False))
# Long thin likely helper/artifact objects.
print('LONG_THIN')
for r in records:
    ds=sorted(r['dims'], reverse=True)
    if ds[0]>35 and ds[1]<0.7:
        print(json.dumps(r, ensure_ascii=False))
print('SCENE_BBOX')
mins=[min((r['center'][i]-r['dims'][i]/2) for r in records) for i in range(3)]
maxs=[max((r['center'][i]+r['dims'][i]/2) for r in records) for i in range(3)]
print(json.dumps({'min':mins,'max':maxs,'dims':[maxs[i]-mins[i] for i in range(3)]}, ensure_ascii=False))
print('INVENTORY_END')

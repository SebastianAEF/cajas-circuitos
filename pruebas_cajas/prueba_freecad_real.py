"""Pruebas con geometría real de FreeCAD para cajas_freecad.py.

Ejecutar:
    "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" pruebas_cajas\\prueba_freecad_real.py

Opcional: variable CAJAS_STEP con otra placa STEP. Si el STEP no existe, esas
pruebas se saltan. Sale con código 1 si alguna prueba falla.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

import cajas_freecad as cf  # noqa: E402
import cajas_modelo as cm  # noqa: E402

V = App.Vector
STEP = os.environ.get("CAJAS_STEP", r"C:\Users\tupin\Downloads\TEC-8A-24V-PID-HC Step File.stp")
HAVE_STEP = Path(STEP).is_file()
FC_PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="cajas_prueba_"))
RESULTS = []
TIMES = {}


def run(name, fn, needs_step=False):
    # Argumentos opcionales: ejecutar solo las pruebas cuyo nombre contenga alguno.
    if sys.argv[1:] and not any(a.lower() in name.lower() for a in sys.argv[1:]):
        return
    if needs_step and not HAVE_STEP:
        RESULTS.append((name, "SKIP", "no existe el STEP de prueba"))
        print(f"SKIP  {name}: no existe {STEP}")
        return
    t = time.time()
    try:
        detail = fn() or ""
        status = "PASS"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        status = "FAIL"
    dt = time.time() - t
    TIMES[name] = dt
    RESULTS.append((name, status, detail))
    print(f"{status}  {name} ({dt:.1f} s) {detail if status == 'PASS' else ''}")
    if status == "FAIL":
        print(detail)


def cfg(**kw):
    return cm.config_from({**kw})


def raw(**kw):
    """Configuración sin validar por la aplicación (simula una macro editada a mano)."""
    return cm.normalize({**kw})


def expect_error(fn, *fragments):
    try:
        fn()
    except ValueError as exc:
        msg = str(exc)
        for f in fragments:
            assert f.lower() in msg.lower(), f"mensaje inesperado: {msg!r} (esperaba {f!r})"
        return msg
    raise AssertionError("no lanzó error")


def check_pieces(r):
    for k in ("base", "lid"):
        s = r[k]
        assert s.isValid(), k + " inválida"
        assert len(s.Solids) == 1, f"{k}: {len(s.Solids)} sólidos"
        assert s.Volume > 0


def inside(shape, x, y, z):
    return shape.isInside(V(x, y, z), 1e-7, True)


_SIZES = {}


def step_size(axis="Z", rot=0.0):
    key = (axis, float(rot) % 360)
    if key not in _SIZES:
        _SIZES[key] = cf.read_board({"step_path": STEP, "up_axis": axis, "rotation": rot})[1]
    return _SIZES[key]


def step_cfg(**kw):
    """Como la GUI: board_x/y/z se rellenan con el size de inspect."""
    base = dict(step_path=STEP, up_axis="Z", rotation=0.0)
    base.update(kw)
    size = step_size(base["up_axis"], base["rotation"])
    base.update(board_x=size[0], board_y=size[1], board_z=size[2])
    return base


# ---------------------------------------------------------------------------

def t_manual_default():
    c = cfg()
    r = cf.build(c)
    check_pieces(r)
    d, ref = r["dimensions"], cm.dimensions(c)
    for k in ref:
        assert abs(d[k] - ref[k]) < 1e-9, (k, d[k], ref[k])
    assert r["tongues"] == 8, r["tongues"]
    seam, ceiling = d["seam"], c["floor"] + d["inner_z"]
    lb = r["lid"].BoundBox
    assert abs(lb.ZMin - seam) < 1e-6 and abs(lb.ZMax - d["outer_z"]) < 1e-6
    # Falda de altura lid_depth: el hueco termina exactamente en la cara inferior del techo.
    assert not inside(r["lid"], 0, 0, ceiling - 0.01) and inside(r["lid"], 0, 0, ceiling + 0.01)
    assert abs((ceiling - seam) - c["lid_depth"]) < 1e-9
    wall_x = d["outer_x"]/2 - c["wall"]/2
    assert abs(r["base"].BoundBox.ZMax - (seam + c["tongue_height"])) < 1e-6
    assert r["collision"] < 1e-6
    return f"dims={d['outer_x']:.1f}x{d['outer_y']:.1f}x{d['outer_z']:.1f} costura={seam:.2f} wx={wall_x:.1f}"


def _slot_walls(r):
    """Material mínimo entre las ranuras y las superficies de la falda (sección horizontal)."""
    fit, d, c = r["fit"], r["dimensions"], r["config"]
    z = d["seam"] + (c["tongue_height"] - fit["bevel_height"])/2
    wires = sorted(r["lid"].slice(V(0, 0, 1), z), key=lambda w: w.Length)
    slots, contours = wires[:-2], wires[-2:]
    assert len(slots) == r["tongues"], (len(slots), r["tongues"])
    return min(s.distToShape(k)[0] for s in slots for k in contours)


def t_fit_measured():
    out = []
    for gap, wall in ((0.15, 3.0), (0.3, 3.0), (0.0, 3.0), (0.2, 4.0)):
        r = cf.build(cfg(clearance=gap, wall=wall))
        check_pieces(r)
        m = r["fit"]["measured_clearance"]
        assert abs(m - gap) < 1e-6, (gap, m)
        sw = _slot_walls(r)
        assert sw >= 0.8 - 1e-6, sw
        assert abs(sw - r["fit"]["slot_side_wall"]) < 1e-6, (sw, r["fit"]["slot_side_wall"])
        out.append(f"juego {gap}->{m:.4f} pared_ranura={sw:.3f} lengüeta={r['fit']['tongue_thickness']:.2f}")
    return "; ".join(out)


def t_insertion():
    c = cfg()
    r = cf.build(c)
    base, lid, th = r["base"], r["lid"], c["tongue_height"]
    closed = base.common(lid).Volume
    assert abs(closed - r["fit_interference"]) < 1e-6 and closed > 0
    # Sin nervios no hay contacto ni cerrada ni a mitad de recorrido.
    plain = cf.build(cfg(rib_interference=0))
    assert plain["fit_interference"] == 0
    vols = []
    for off in (0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8):
        moved = plain["lid"].copy()
        moved.translate(V(0, 0, off))
        vols.append(plain["base"].common(moved).Volume)
    assert max(vols) < 1e-6, vols
    # Con nervios: solo hay contacto en el tramo final (tapa a < 0,7 mm del cierre).
    rib = []
    for off in (0.0, 0.3, 0.6, 0.9, 1.5):
        moved = lid.copy()
        moved.translate(V(0, 0, off))
        rib.append(round(base.common(moved).Volume, 5))
    assert rib[0] > 0 and rib[-1] == 0 and rib[-2] == 0, rib
    return f"interferencia cerrada={closed:.4f} mm³ (nervios), por desplazamiento={rib}"


def t_apoyada():
    r = cf.build(cfg(closure="apoyada", wall=2.0, radius=2.0))
    check_pieces(r)
    assert r["tongues"] == 0 and r["fit_interference"] == 0
    assert any("apoyada" in w.lower() for w in r["warnings"])
    assert abs(r["base"].BoundBox.ZMax - r["dimensions"]["seam"]) < 1e-6
    assert r["base"].common(r["lid"]).Volume < 1e-6
    return "sin lengüetas, base y tapa sin intersección"


def t_step_orientations():
    out = []
    sizes = {}
    for axis, rot in (("Z", 0), ("Z", 90), ("Z", 37), ("X", 0), ("Y", 0), ("X", 37)):
        t = time.time()
        shape, size, info = cf.read_board({"step_path": STEP, "up_axis": axis, "rotation": rot})
        _SIZES[(axis, float(rot) % 360)] = size
        b = shape.optimalBoundingBox()
        assert abs(b.ZMin) < 1e-6 and abs(b.XMin + b.XMax) < 1e-6 and abs(b.YMin + b.YMax) < 1e-6, b
        assert abs(b.XLength - size[0]) < 1e-6 and abs(b.ZLength - size[2]) < 1e-6
        sizes[(axis, rot)] = size
        out.append(f"{axis}/{rot}: {size[0]:.2f}x{size[1]:.2f}x{size[2]:.2f} ({time.time()-t:.1f}s)")
    z0, z90, x0, y0 = sizes[("Z", 0)], sizes[("Z", 90)], sizes[("X", 0)], sizes[("Y", 0)]
    assert abs(z0[0] - 44) < 0.01 and abs(z0[1] - 59) < 0.01 and abs(z0[2] - 18.805) < 0.01, z0
    assert abs(z90[0] - z0[1]) < 1e-3 and abs(z90[1] - z0[0]) < 1e-3
    assert abs(x0[2] - z0[0]) < 1e-3, "con eje X vertical, Z debe medir el XLength original"
    assert abs(y0[2] - z0[1]) < 1e-3, "con eje Y vertical, Z debe medir el YLength original"
    assert abs(sizes[("Z", 37)][2] - z0[2]) < 1e-3
    return "; ".join(out)


def t_step_inspect_worker():
    req = {"action": "inspect", "config": {"step_path": STEP, "up_axis": "Z", "rotation": 0,
                                           "board_x": 1, "board_y": 1, "board_z": 1}}
    res, dt = worker(req)
    assert res["ok"], res
    r = res["result"]
    assert abs(r["size"][0] - 44) < 0.01 and r["solids"] == 176, r["size"]
    assert 100 < len(r["mesh"]) <= 6000, len(r["mesh"])
    TIMES["worker inspect STEP"] = dt
    return f"size={[round(v, 3) for v in r['size']]} triángulos={len(r['mesh'])} sólidos={r['solids']} {dt:.1f}s"


def _probe_spots(board_shape, bottom_gap):
    """Busca un punto libre y uno ocupado por la placa en la franja z∈[0, 1] (placa normalizada)."""
    b = board_shape.optimalBoundingBox()
    free = hit = None
    for i in range(1, 9):
        for j in range(1, 9):
            x = b.XMin + (b.XMax - b.XMin)*i/9
            y = b.YMin + (b.YMax - b.YMin)*j/9
            cyl = Part.makeCylinder(1.0, 1.0, V(x, y, 0))
            vol = sum(s.common(cyl).Volume for s in board_shape.Solids if s.BoundBox.intersect(cyl.BoundBox))
            if vol < 1e-9 and free is None:
                free = (x, y)
            if vol > 0.05 and hit is None:
                hit = (x, y)
        if free and hit:
            break
    return free, hit


def t_step_build_supports():
    board, size, _ = cf.read_board({"step_path": STEP})
    free, hit = _probe_spots(board, 3.0)
    assert free and hit, (free, hit)
    t = time.time()
    r = cf.build(cfg(**step_cfg()))
    t_plain = time.time() - t
    check_pieces(r)
    assert abs(r["config"]["board_x"] - 44) < 0.01
    # Soporte dentro de la envolvente pero en zona libre: sin colisión real (no basta la caja envolvente).
    s_ok = {"x": free[0], "y": free[1], "height": 4.0, "diameter": 2.0, "hole": 0.0}
    t = time.time()
    r2 = cf.build(cfg(**step_cfg(supports=[s_ok])))
    t_sup = time.time() - t
    check_pieces(r2)
    assert r2["collision"] < 1e-6
    s_bad = dict(s_ok, x=hit[0], y=hit[1])
    msg = expect_error(lambda: cf.build(cfg(**step_cfg(supports=[s_bad]))), "intersecta")
    return (f"build {t_plain:.1f}s, con soporte {t_sup:.1f}s, método={r2['collision_method']}; "
            f"libre={tuple(round(v, 1) for v in free)} ocupado={tuple(round(v, 1) for v in hit)}: {msg[:60]}")


def t_step_rotated_build():
    t = time.time()
    r = cf.build(cfg(**step_cfg(up_axis="X", rotation=37)))
    check_pieces(r)
    return f"X/37 -> placa {[round(r['config'][k], 2) for k in ('board_x', 'board_y', 'board_z')]} {time.time()-t:.1f}s"


def t_ports_each_face():
    c0 = cfg()
    d = cm.dimensions(c0)
    seam = d["seam"]
    ports = [
        {"side": "frente", "u": 0, "v": 8, "width": 10, "height": 5},
        {"side": "fondo", "u": -10, "v": 8, "width": 8, "height": 4},
        {"side": "izquierda", "u": 5, "v": 9, "width": 6, "height": 6},
        {"side": "derecha", "u": -5, "v": 7, "width": 12, "height": 4},
        {"side": "tapa", "u": 10, "v": -10, "width": 15, "height": 8},
        {"side": "derecha", "u": 0, "v": seam, "width": 8, "height": 6},   # cruza la costura
    ]
    r = cf.build(cfg(ports=ports))
    check_pieces(r)
    base, lid, wall = r["base"], r["lid"], c0["wall"]
    ox, oy = d["outer_x"]/2, d["outer_y"]/2
    assert not inside(base, 0, -oy + wall/2, 8)
    assert not inside(base, -10, oy - wall/2, 8)
    assert not inside(base, -ox + wall/2, 5, 9)
    assert not inside(base, ox - wall/2, -5, 7)
    assert not inside(lid, 10, -10, d["outer_z"] - c0["lid"]/2)
    assert not inside(base, ox - wall/2, 0, seam - 1) and not inside(lid, ox - wall/2, 0, seam + 1)
    assert inside(base, ox - wall/2, 0, seam - 4) and inside(lid, ox - wall/2, 0, d["outer_z"] - 1.3)
    assert any("cruza la unión" in w for w in r["warnings"])
    return f"lengüetas={r['tongues']} avisos={r['warnings']}"


def t_port_blocks_tongue():
    c0 = cfg()
    d = cm.dimensions(c0)
    free = d["outer_x"]/2 - max(c0["radius"] + 2, c0["wall"] + 2)
    p = {"side": "frente", "u": free*0.53, "v": d["seam"], "width": 6, "height": 4}
    r = cf.build(cfg(ports=[p]))
    check_pieces(r)
    # La abertura recorta la lengüeta (no la elimina ni impide calcular) y se avisa.
    assert r["tongues"] == 8 and any("recorta una lengüeta" in w for w in r["warnings"])
    many = [{"side": s, "u": 0, "v": d["seam"], "width": 60, "height": 4} for s in ("frente", "fondo", "izquierda")]
    r = cf.build(cfg(ports=many))
    check_pieces(r)
    avisos = [w for w in r["warnings"] if "recorta" in w]
    assert len(avisos) == 3, avisos
    return f"lengüeta recortada con aviso; tres caras cortadas -> {len(avisos)} avisos, caja válida"


def t_supports_manual():
    c0 = cfg()
    s = {"x": 20, "y": 20, "height": 3.0, "diameter": 6, "hole": 2.5}
    r = cf.build(cfg(supports=[s]))
    check_pieces(r)
    base, fl = r["base"], c0["floor"]
    assert inside(base, 20, 20, fl/2), "el agujero perfora el piso"
    assert not inside(base, 20, 20, fl + 1.0), "falta el agujero"
    assert inside(base, 22.5, 20, fl + 2.0), "falta el soporte"
    assert abs(base.common(Part.makeCylinder(3, 3, V(20, 20, fl))).Volume
               - Part.makeCylinder(3, 3, V(20, 20, fl)).cut(Part.makeCylinder(1.25, 3, V(20, 20, fl))).Volume) < 1e-3
    # Soporte más alto que la holgura inferior bajo la envolvente manual: colisión.
    bad = dict(s, height=4.0)
    m1 = expect_error(lambda: cf.build(cfg(supports=[bad])), "intersecta")
    # Soporte junto a la pared: la aplicación y el constructor lo rechazan.
    wall_s = {"x": 34.5, "y": 0, "height": 2, "diameter": 6, "hole": 0}
    m2 = expect_error(lambda: cf.build(raw(supports=[wall_s])), "Soporte 1", "pared")
    expect_error(lambda: cfg(supports=[wall_s]), "Soporte 1")
    m3 = expect_error(lambda: cf.build(raw(supports=[dict(s, height=20)])), "plano de unión")
    return f"{m1[:50]} | {m2} | {m3}"


def t_validations():
    d = cm.dimensions(cfg())
    cases = [
        (dict(lid_depth=30), "profundidad de tapa"),
        (dict(wall=2.5), "3 mm"),
        (dict(radius=8, margin=0), "radio"),   # pared de 0,93 mm en la esquina
        (dict(tongue_height=5), "lengüeta"),
        (dict(ports=[{"side": "frente", "u": 0, "v": d["seam"] - 2.8, "width": 6, "height": 4.8}]), "tira"),
        (dict(ports=[{"side": "fondo", "u": 0, "v": d["outer_z"] - 2.8, "width": 6, "height": 5}]), "tira"),
        (dict(ports=[{"side": "tapa", "u": 0, "v": 0, "width": 76, "height": 10}]), "área interior"),
        (dict(ports=[{"side": "frente", "u": -5, "v": 8, "width": 6, "height": 4},
                     {"side": "frente", "u": 1.5, "v": 8, "width": 6, "height": 4}]), "tabique"),
        (dict(ports=[{"side": "frente", "u": 0, "v": 1, "width": 6, "height": 4}]), "piso"),
        (dict(supports=[{"x": 0, "y": 0, "height": 2, "diameter": 3, "hole": 3}]), "agujero"),
    ]
    msgs = []
    for kw, frag in cases:
        msgs.append(expect_error(lambda kw=kw: cf.build(raw(**kw)), frag))
    # Una abertura que divide la pieza: corte completo de las cuatro paredes.
    split = [{"side": s, "u": 0, "v": 9.4,
              "width": d["outer_x"], "height": 12} for s in ("frente", "fondo", "izquierda", "derecha")]
    msgs.append(expect_error(lambda: cf.build(raw(closure="apoyada", ports=split)), "dividida"))
    return f"{len(msgs)} rechazos; ej.: " + " | ".join(m[:70] for m in msgs[-3:])


def worker(req, python=None):
    folder = Path(tempfile.mkdtemp(prefix="w_", dir=TMP))
    rq, rs = folder/"request.json", folder/"response.json"
    rq.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    t = time.time()
    subprocess.run([python or FC_PY, str(ROOT/"cajas_freecad.py"), str(rq), str(rs)],
                   capture_output=True, timeout=600,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    dt = time.time() - t
    return json.loads(rs.read_text(encoding="utf-8")), dt


def t_worker_subprocess():
    c = cfg(ports=[{"side": "frente", "u": 0, "v": 8, "width": 10, "height": 5}],
            supports=[{"x": 20, "y": 20, "height": 3, "diameter": 6, "hole": 2.5}])
    out = []
    for view in ("separada", "cerrada", "explosionada"):
        res, dt = worker({"action": "preview", "config": dict(c, view=view)})
        assert res["ok"], res.get("error")
        r = res["result"]
        assert set(r["meshes"]) == {"base", "lid", "board"}
        assert all(0 < len(m) <= 6000 for m in r["meshes"].values()), {k: len(m) for k, m in r["meshes"].items()}
        assert set(r["dimensions"]) == {"inner_x", "inner_y", "inner_z", "outer_x", "outer_y", "outer_z", "seam",
                                     "ceiling", "lid_skirt"}
        out.append(f"{view} {dt:.1f}s tri={[len(m) for m in r['meshes'].values()]}")
        TIMES["worker preview manual " + view] = dt
        # Coherencia vista/CAD: la malla viene de display_shapes, igual que el FCStd.
        ref = cf.display_shapes(cf.build(dict(c, view=view)), view)[1].BoundBox
        pts = [p for tri in r["meshes"]["lid"] for p in tri]
        for k, f in ((0, min), (2, min), (0, max), (2, max)):
            got = f(p[k] for p in pts)
            want = {(0, min): ref.XMin, (2, min): ref.ZMin, (0, max): ref.XMax, (2, max): ref.ZMax}[(k, f)]
            assert abs(got - want) < 0.05, (view, k, got, want)
    bad, _ = worker({"action": "preview", "config": dict(c, step_path=str(TMP/"no_existe.step"))})
    assert not bad["ok"] and "No se encuentra" in bad["error"] and bad["traceback"]
    bad2, _ = worker({"action": "volar", "config": c})
    assert not bad2["ok"] and "desconocida" in bad2["error"]
    folder = Path(tempfile.mkdtemp(prefix="w_", dir=TMP))
    (folder/"request.json").write_text("{esto no es json", encoding="utf-8")
    subprocess.run([FC_PY, str(ROOT/"cajas_freecad.py"), str(folder/"request.json"), str(folder/"response.json")],
                   capture_output=True, timeout=120)
    bad3 = json.loads((folder/"response.json").read_text(encoding="utf-8"))
    assert not bad3["ok"]
    out.append("errores: " + bad["error"][:40] + " / " + bad2["error"])
    # Sin FreeCAD (Python normal): la respuesta también se escribe.
    normal = os.environ.get("CAJAS_PYTHON_NORMAL",
                            r"C:\Users\tupin\AppData\Local\Programs\Python\Python313\python.exe")
    if not Path(normal).exists():
        out.append("sin FreeCAD: omitido (no hay Python normal)")
    else:
        bad4, _ = worker({"action": "preview", "config": c}, python=normal)
        assert not bad4["ok"] and "FreeCAD" in bad4["error"], bad4
        out.append("sin FreeCAD: " + bad4["error"][:50])
    return "; ".join(out)


def t_worker_step_preview():
    c = cfg(**step_cfg(up_axis="Z", rotation=37))
    res, dt = worker({"action": "preview", "config": c})
    assert res["ok"], res.get("error")
    r = res["result"]
    assert all(0 < len(m) <= 6000 for m in r["meshes"].values())
    TIMES["worker preview STEP"] = dt
    return f"{dt:.1f}s size={[round(v, 2) for v in r['size']]} tri={[len(m) for m in r['meshes'].values()]}"


def t_export_unicode():
    folder = TMP/"Prueba cajas ñandú"/"salida 1"
    c = cfg(name="Caja ñandú", export_stl=True, export_step=True,
            ports=[{"side": "frente", "u": 0, "v": 8, "width": 10, "height": 5}])
    res, dt = worker({"action": "export", "config": c, "folder": str(folder)})
    assert res["ok"], res.get("error")
    TIMES["worker export manual"] = dt
    files = res["result"]["files"]
    names = sorted(Path(f).name for f in files)
    assert names == sorted(["Caja.FCStd", "Base.stl", "Tapa.stl", "Base.step", "Tapa.step"]), names
    for f in files:
        assert Path(f).stat().st_size > 1000, f
    doc = App.openDocument(str(folder/"Caja.FCStd"))
    try:
        objs = {o.Name for o in doc.Objects}
        assert {"Base", "Tapa", "Placa", "Parametros"} <= objs, objs
        conf = json.loads(doc.getObject("Parametros").ConfiguracionJSON)
        assert conf["name"] == "Caja ñandú"
        assert doc.getObject("Base").Shape.isValid() and doc.getObject("Tapa").Shape.Volume > 0
        assert abs(doc.getObject("Parametros").AltoExterior.Value - res["result"]["dimensions"]["outer_z"]) < 1e-6
    finally:
        App.closeDocument(doc.Name)
    tapa = Part.read(str(folder/"Tapa.step"))
    b = tapa.BoundBox
    assert abs(b.ZMin) < 1e-3, "tapa no apoyada en z=0"
    assert tapa.isInside(V(0, 0, c["lid"]/2), 1e-6, True), "el techo de la tapa no queda abajo"
    base = Part.read(str(folder/"Base.step"))
    assert abs(base.BoundBox.ZMin) < 1e-3 and len(base.Solids) == 1
    import Mesh
    m = Mesh.Mesh(str(folder/"Tapa.stl"))
    assert m.CountFacets > 100 and abs(m.BoundBox.ZMin) < 1e-3
    # Rechazo de sobrescritura: nada cambia.
    before = {f: Path(f).stat().st_mtime_ns for f in files}
    res2, _ = worker({"action": "export", "config": c, "folder": str(folder)})
    assert not res2["ok"] and "ya contiene" in res2["error"], res2
    assert before == {f: Path(f).stat().st_mtime_ns for f in files}
    return f"{dt:.1f}s, {names}; sobrescritura rechazada: {res2['error'][:60]}"


def t_export_step_board():
    folder = TMP/"export step"
    c = cfg(**step_cfg(export_stl=True, export_step=False))
    res, dt = worker({"action": "export", "config": c, "folder": str(folder)})
    assert res["ok"], res.get("error")
    TIMES["worker export STEP"] = dt
    doc = App.openDocument(str(folder/"Caja.FCStd"))
    try:
        assert len(doc.getObject("Placa").Shape.Solids) == 176
    finally:
        App.closeDocument(doc.Name)
    return f"{dt:.1f}s files={[Path(f).name for f in res['result']['files']]}"


def t_macro():
    folder = TMP/"macro ñ"
    folder.mkdir()
    out = []
    for label, c in (("manual", cfg(name="Macro ñ", ports=[{"side": "tapa", "u": 0, "v": 0, "width": 10, "height": 10}])),
                     ("step", cfg(**step_cfg(name="Macro STEP", up_axis="Y", rotation=90)) if HAVE_STEP else None)):
        if c is None:
            continue
        macro = folder/f"Caja {label}.FCMacro"
        cm.write_macro(macro, c)
        try:
            cm.write_macro(macro, c)
            raise AssertionError("write_macro sobrescribió sin overwrite")
        except FileExistsError:
            pass
        runner = folder/f"run_{label}.py"
        runner.write_text(
            "import sys, json\n"
            "import FreeCAD as App\n"
            f"src = open({str(macro)!r}, encoding='utf-8').read()\n"
            "g = {'__name__': '__main__'}\n"
            f"exec(compile(src, {str(macro)!r}, 'exec'), g)\n"
            "doc = App.ActiveDocument\n"
            "names = sorted(o.Name for o in doc.Objects)\n"
            "print(json.dumps({'doc': doc.Name, 'file': doc.FileName, 'names': names,"
            " 'valid': doc.getObject('Base').Shape.isValid() and doc.getObject('Tapa').Shape.isValid()}))\n",
            encoding="utf-8")
        before = sorted(p.name for p in folder.iterdir())
        p = subprocess.run([FC_PY, str(runner)], capture_output=True, timeout=600, cwd=str(TMP))
        assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-2000:]
        info = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        assert {"Base", "Tapa", "Placa", "Parametros", "Caja"} <= set(info["names"]), info
        assert info["file"] == "" and info["valid"], info
        assert before == sorted(q.name for q in folder.iterdir()), "la macro creó archivos"
        out.append(f"{label}: {info['names']}")
    return "; ".join(out)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(f"FreeCAD {'.'.join(App.Version()[:3])}; STEP {'disponible' if HAVE_STEP else 'NO disponible'}; tmp={TMP}")
    run("caja manual por defecto", t_manual_default)
    run("juego del cierre medido", t_fit_measured)
    run("inserción vertical", t_insertion)
    run("tapa apoyada", t_apoyada)
    run("aberturas en cada cara + tapa + costura", t_ports_each_face)
    run("abertura bloquea lengüetas", t_port_blocks_tongue)
    run("soportes (manual)", t_supports_manual)
    run("validaciones que deben fallar", t_validations)
    run("STEP orientaciones Z/X/Y y giros", t_step_orientations, True)
    run("STEP inspect por worker (config mínima)", t_step_inspect_worker, True)
    run("STEP caja + soportes/colisión real", t_step_build_supports, True)
    run("STEP X + 37° caja", t_step_rotated_build, True)
    run("worker subprocess preview + errores", t_worker_subprocess)
    run("worker preview STEP", t_worker_step_preview, True)
    run("export ruta con espacios/acentos + sobrescritura", t_export_unicode)
    run("export con STEP", t_export_step_board, True)
    run("macro autónoma", t_macro)
    print("\nResumen")
    for name, status, _ in RESULTS:
        print(f"  {status:4}  {name}  ({TIMES.get(name, 0):.1f} s)")
    extra = {k: v for k, v in TIMES.items() if k.startswith("worker ")}
    for k, v in extra.items():
        print(f"  tiempo {k}: {v:.1f} s")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS) - len(fails)} OK/SKIP, {len(fails)} FAIL")
    if not fails:
        shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

"""Pruebas de la caché de placas y de la sincronización del documento (cajas_freecad).

Ejecutar con el Python de FreeCAD (sin GUI):
    "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" pruebas_cajas\\prueba_cache_freecad.py

Opcional: CAJAS_STEP con otra placa. Las pruebas que necesitan STEP se saltan si
no existe. El STEP original nunca se modifica: se trabaja sobre copias en un
directorio temporal propio. Sale con código 1 si alguna prueba falla.
"""
import json
import os
import shutil
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
TMP = Path(tempfile.mkdtemp(prefix="cajas_cache_prueba_"))
RESULTS = []
TIMES = {}
SHARED = {}          # caché y resultados compartidos entre pruebas


def run(name, fn, needs_step=False):
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
    print(f"{status}  {name} ({dt:.1f} s) {detail if status == 'PASS' else ''}", flush=True)
    if status == "FAIL":
        print(detail, flush=True)


def shape_sig(shape):
    """Firma geométrica: BoundBox, volumen y hash de vértices (con ubicación aplicada)."""
    b = shape.BoundBox
    pts = sorted((round(v.X, 6), round(v.Y, 6), round(v.Z, 6)) for v in shape.Vertexes)
    return (tuple(round(x, 6) for x in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)),
            round(shape.Volume, 4), hash(tuple(pts)), str(shape.Placement))


def step_cfg(cache, path=STEP, **kw):
    base = dict(step_path=str(path), up_axis="Z", rotation=0.0)
    base.update(kw)
    _, size, _ = cache.get(base)
    base.update(board_x=size[0], board_y=size[1], board_z=size[2])
    return cm.config_from(base)


def tmp_step_files():
    """Archivos/directorios temporales que crearía la copia ASCII de _read_step."""
    return sorted(p.name for p in Path(tempfile.gettempdir()).glob("cajas_step_*"))


def bb_close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(
        (a.XMin, a.YMin, a.ZMin, a.XMax, a.YMax, a.ZMax), (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)))


# ---------------------------------------------------------------------------

def t_una_lectura():
    cache = cf.BoardCache()
    SHARED["cache"] = cache
    tmp_before = tmp_step_files()
    t = time.time()
    c0 = step_cfg(cache)
    r0 = cf.build(c0, cache=cache)
    first = time.time() - t
    assert cache.read_count == 1, cache.stats()
    sig0 = shape_sig(r0["board"])
    variants = [dict(top_gap=4.0), dict(margin=4.0, wall=3.5),
                dict(ports=[{"side": "frente", "u": 0, "v": 9, "width": 10, "height": 5}]),
                dict(supports=[{"x": -14, "y": -20, "height": 2.0, "diameter": 3, "hole": 0}]),
                dict(clearance=0.25, bottom_gap=4.0)]
    times = []
    for kw in variants:
        t = time.time()
        r = cf.build(cm.config_from(dict(c0, **kw)), cache=cache)
        times.append(time.time() - t)
        assert r["board_key"] == r0["board_key"]
        assert abs(r["board_offset_z"] - (r["config"]["floor"] + r["config"]["bottom_gap"])) < 1e-12
        assert r["base"].isValid() and r["lid"].isValid()
    assert cache.read_count == 1, cache.stats()
    assert tmp_step_files() == tmp_before, "se crearon temporales STEP"
    # Mismo resultado que sin caché (dimensiones, placa y piezas).
    ref = cf.build(c0)
    assert ref["board_key"] == r0["board_key"], (ref["board_key"], r0["board_key"])
    assert shape_sig(ref["board"])[:3] == sig0[:3]
    for k in ref["dimensions"]:
        assert abs(ref["dimensions"][k] - r0["dimensions"][k]) < 1e-9
    for k in ("base", "lid"):
        assert abs(ref[k].Volume - r0[k].Volume) < 1e-6
    assert ref["collision"] == r0["collision"] and ref["tongues"] == r0["tongues"]
    TIMES["build primera carga (con Part.read)"] = first
    TIMES["build siguiente (media de 5, caché)"] = sum(times)/len(times)
    SHARED["c0"], SHARED["r0"] = c0, r0
    return (f"primera {first:.2f}s; siguientes {', '.join(f'{x:.2f}' for x in times)} s; "
            f"stats={cache.stats()}")


def t_formas_intactas():
    cache = SHARED["cache"]
    (fkey, (orig, _)), = list(cache._originals.items())[:1]
    before = shape_sig(orig)
    okey = next(k for k in cache._oriented if k[0] == fkey)
    obefore = shape_sig(cache._oriented[okey][0])
    # Orientaciones nuevas y builds: el original y la orientación cacheada no cambian.
    for axis, rot in (("X", 0), ("Z", 37)):
        cache.get(dict(step_path=STEP, up_axis=axis, rotation=rot))
    r = cf.build(SHARED["c0"], cache=cache)
    r["board"].translate(V(5, 5, 5))           # el llamador traslada lo que recibe
    r["board_local"].rotate(V(), V(0, 0, 1), 30)
    s, _, _ = cache.get(SHARED["c0"])
    s.translate(V(0, 0, 100))
    assert shape_sig(orig) == before, "el original en caché cambió"
    assert shape_sig(cache._oriented[okey][0]) == obefore, "la orientación en caché cambió"
    s2, size, _ = cache.get(SHARED["c0"])
    b = s2.optimalBoundingBox()
    assert abs(b.ZMin) < 1e-6 and abs(b.XMin + b.XMax) < 1e-6 and abs(b.ZLength - size[2]) < 1e-6
    return f"original y orientación sin cambios; read_count={cache.read_count}"


def t_orientaciones():
    cache = cf.BoardCache(max_originals=2, max_oriented=2)
    out = []
    sizes = {}
    for axis in ("Z", "X", "Y", "Z"):
        t = time.time()
        shape, size, info = cache.get(dict(step_path=STEP, up_axis=axis, rotation=0))
        sizes[axis] = size
        b = shape.optimalBoundingBox()
        assert abs(b.ZMin) < 1e-6 and abs(b.XMin + b.XMax) < 1e-6 and abs(b.YMin + b.YMax) < 1e-6
        assert info["board_key"].endswith(f"|{axis}|0.0"), info["board_key"]
        assert len(cache._oriented) <= 2
        out.append(f"{axis} {time.time()-t:.2f}s")
    st = cache.stats()
    assert st["read_count"] == 1, st
    # Z, X, Y (expulsa Z), Z de nuevo → 4 orientaciones calculadas, ningún acierto.
    assert st["orient_count"] == 4 and st["hits"] == 0 and st["oriented"] == 2, st
    cache.get(dict(step_path=STEP, up_axis="Z", rotation=360))     # 360 ≡ 0 → acierto
    assert cache.stats()["hits"] == 1 and cache.read_count == 1
    z, x, y = sizes["Z"], sizes["X"], sizes["Y"]
    assert abs(z[0] - 44) < 0.01 and abs(z[1] - 59) < 0.01 and abs(z[2] - 18.805) < 0.01, z
    assert abs(x[2] - z[0]) < 1e-3 and abs(y[2] - z[1]) < 1e-3
    # Igual que read_board sin caché.
    _, rs, ri = cf.read_board(dict(step_path=STEP, up_axis="X", rotation=0))
    assert all(abs(a - b) < 1e-9 for a, b in zip(rs, x))
    return "; ".join(out) + f"; stats={cache.stats()}"


def t_cambio_en_disco():
    folder = TMP/"Prueba caché ñandú"
    folder.mkdir(parents=True)
    copy = folder/"placa uno.stp"
    shutil.copyfile(STEP, copy)
    cache = cf.BoardCache()
    cfg = dict(step_path=str(copy), up_axis="Z", rotation=0)
    _, s1, i1 = cache.get(cfg)
    assert cache.read_count == 1
    cache.get(cfg)
    assert cache.read_count == 1 and cache.hits == 1
    st = copy.stat()
    os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    _, s2, i2 = cache.get(cfg)
    assert cache.read_count == 2, cache.stats()
    assert i2["board_key"] != i1["board_key"]
    assert len(cache._originals) == 1, "debe descartar la entrada vieja de la misma ruta"
    size_before = copy.stat().st_size
    with open(copy, "ab") as f:
        f.write(b"\r\n\r\n")          # líneas vacías tras END-ISO-10303-21;
    assert copy.stat().st_size == size_before + 4
    _, s3, i3 = cache.get(cfg)
    assert cache.read_count == 3, cache.stats()
    assert i3["board_key"] not in (i1["board_key"], i2["board_key"])
    assert all(abs(a - b) < 1e-9 for a, b in zip(s1, s3)), (s1, s3)
    # build con la ruta con acentos.
    r = cf.build(cm.config_from(dict(cfg, board_x=s3[0], board_y=s3[1], board_z=s3[2])), cache=cache)
    assert cache.read_count == 3 and r["board_key"] == i3["board_key"]
    SHARED["copy"] = copy
    return f"mtime y tamaño provocan relectura; temporales copia ASCII={cache.temp_copies}; {cache.stats()}"


def t_lru_originales():
    folder = TMP/"Prueba caché ñandú"
    paths = []
    for i in range(3):
        p = folder/f"placa lru {i}.stp"
        shutil.copyfile(STEP, p)
        paths.append(p)
    cache = cf.BoardCache(max_originals=2, max_oriented=4)
    for p in paths:
        cache.get(dict(step_path=str(p)))
        cache.get(dict(step_path=str(p), up_axis="X"))
        assert len(cache._originals) <= 2 and len(cache._oriented) <= 4, cache.stats()
    assert cache.read_count == 3
    # Las orientaciones del original expulsado (0) también se expulsaron.
    keys0 = {k[0][0] for k in cache._oriented}
    assert os.path.normcase(os.path.abspath(str(paths[0]))) not in keys0
    assert len(cache._oriented) == 4
    cache.get(dict(step_path=str(paths[2])))            # acierto, sin leer
    assert cache.read_count == 3
    cache.get(dict(step_path=str(paths[0])))            # se había expulsado: relee
    assert cache.read_count == 4 and len(cache._originals) == 2
    cache.clear()
    assert cache.stats()["originals"] == 0 and cache.stats()["oriented"] == 0
    return f"{cache.stats()}"


def t_sin_temporales():
    cache = SHARED["cache"]
    before = tmp_step_files()
    reads = cache.read_count
    calls = {"n": 0}
    orig = cf.tempfile.mkdtemp

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    cf.tempfile.mkdtemp = spy
    try:
        for gap in (3.0, 3.5, 4.0):
            cf.build(cm.config_from(dict(SHARED["c0"], top_gap=gap)), cache=cache)
    finally:
        cf.tempfile.mkdtemp = orig
    assert calls["n"] == 0, calls
    assert tmp_step_files() == before
    assert cache.read_count == reads
    return f"mkdtemp llamado {calls['n']} veces en 3 cálculos; temp_copies={cache.temp_copies}"


def _sync_case(cache, c, label):
    doc = App.newDocument("SyncPrueba")
    try:
        state = {}
        r1 = cf.build(c, cache=cache)
        o1 = cf.sync_document(doc, r1, view="cerrada", state=state)
        names = {o.Name for o in doc.Objects}
        assert {"CajaBase", "CajaTapa", "CajaPlaca", "CajaParametros"} <= names, names
        assert o1["board_reassigned"] is True and state["board_key"] == r1["board_key"]
        assert doc.getObject("CajaPlaca").Label.startswith("Placa STEP" if c["step_path"] else "Envolvente")
        board_obj = doc.getObject("CajaPlaca")
        sh1 = board_obj.Shape
        # La placa se ve donde la deja build.
        assert bb_close(board_obj.Shape.BoundBox, r1["board"].BoundBox, 1e-6), (board_obj.Shape.BoundBox,
                                                                               r1["board"].BoundBox)
        # Otra holgura (cambia la altura de la placa): no se reasigna, solo Placement.
        r2 = cf.build(cm.config_from(dict(c, top_gap=c["top_gap"] + 1, bottom_gap=c["bottom_gap"] + 0.5)),
                      cache=cache)
        o2 = cf.sync_document(doc, r2, view="cerrada", state=state)
        assert o2["board_reassigned"] is False, o2
        sh2 = board_obj.Shape
        assert sh2.isPartner(sh1), "la forma de la placa cambió"
        assert abs(board_obj.Placement.Base.z - r2["board_offset_z"]) < 1e-12
        assert bb_close(sh2.BoundBox, r2["board"].BoundBox, 1e-6)
        params = json.loads(doc.getObject("CajaParametros").ConfiguracionJSON)
        assert abs(params["top_gap"] - (c["top_gap"] + 1)) < 1e-12
        assert abs(doc.getObject("CajaParametros").AltoExterior.Value - r2["dimensions"]["outer_z"]) < 1e-9
        # Las tres vistas coinciden con display_shapes.
        for view in ("cerrada", "explosionada", "separada"):
            cf.sync_document(doc, r2, view=view, state=state)
            ref = cf.display_shapes(r2, view)
            for name, k in (("CajaTapa", 1), ("CajaBase", 0), ("CajaPlaca", 2)):
                assert bb_close(doc.getObject(name).Shape.BoundBox, ref[k].BoundBox), (view, name)
            # apply_view: mismo resultado sin tocar formas.
            other = "separada" if view != "separada" else "cerrada"
            cf.apply_view(doc, r2, other)
            ov = cf.apply_view(doc, r2, view, visible={"base": True, "lid": True, "board": False},
                               transparency={"base": 40, "lid": 0})
            assert ov["board_reassigned"] is False
            assert bb_close(doc.getObject("CajaTapa").Shape.BoundBox, ref[1].BoundBox), ("apply_view", view)
            assert doc.getObject("CajaPlaca").Visibility is False
        # Sin state: la clave se guarda en el objeto.
        o3 = cf.sync_document(doc, r2, view="cerrada")
        assert o3["board_reassigned"] is False, o3
        # Vista no válida.
        try:
            cf.sync_document(doc, r2, view="volando", state=state)
            raise AssertionError("aceptó una vista no válida")
        except cf.CajaError:
            pass
        out = f"{label}: creado={o1['created']}"
        if c["step_path"]:
            # Cambio de orientación: se reasigna.
            c90 = step_cfg(cache, rotation=90)
            r3 = cf.build(c90, cache=cache)
            o4 = cf.sync_document(doc, r3, view="separada", state=state)
            assert o4["board_reassigned"] is True and state["board_key"] == r3["board_key"]
            assert bb_close(doc.getObject("CajaPlaca").Shape.BoundBox, r3["board"].BoundBox)
            # show_board con la clave de get() y luego sync: no reasigna.
            shape, size, info = cache.get(dict(step_path=STEP, up_axis="Y", rotation=0))
            sb = cf.show_board(doc, shape, info["board_key"], state)
            assert sb["board_reassigned"] is True
            assert not doc.getObject("CajaBase").Visibility and not doc.getObject("CajaTapa").Visibility
            sb2 = cf.show_board(doc, shape, info["board_key"], state, offset_z=1.0)
            assert sb2["board_reassigned"] is False
            cy = cm.config_from(dict(step_path=STEP, up_axis="Y", rotation=0,
                                     board_x=size[0], board_y=size[1], board_z=size[2]))
            ry = cf.build(cy, cache=cache)
            assert ry["board_key"] == info["board_key"]
            o5 = cf.sync_document(doc, ry, state=state)
            assert o5["board_reassigned"] is False, o5
            assert doc.getObject("CajaBase").Visibility and doc.getObject("CajaTapa").Visibility
            out += "; orientación nueva reasigna; show_board→sync no reasigna"
        else:
            r4 = cf.build(cm.config_from(dict(c, board_x=c["board_x"] + 2)), cache=cache)
            o4 = cf.sync_document(doc, r4, state=state)
            assert o4["board_reassigned"] is True and r4["board_key"].startswith("manual:")
            out += f"; medida manual nueva reasigna ({r4['board_key']})"
        return out
    finally:
        App.closeDocument(doc.Name)


def t_sync_step():
    return _sync_case(SHARED["cache"], SHARED["c0"], "STEP")


def t_sync_manual():
    return _sync_case(cf.BoardCache(), cm.config_from({}), "manual")


def t_export_cache():
    cache = SHARED["cache"]
    reads = cache.read_count
    c = cm.config_from(dict(SHARED["c0"], name="Caja ñandú", export_stl=True, export_step=True))
    r = cf.build(c, cache=cache)
    folder = TMP/"Exportación ñandú"/"salida 1"
    t = time.time()
    files = cf.export_result(r, folder)
    dt = time.time() - t
    TIMES["export_result (solo exportar)"] = dt
    assert cache.read_count == reads, cache.stats()
    names = sorted(Path(f).name for f in files)
    assert names == sorted(["Caja.FCStd", "Base.stl", "Tapa.stl", "Base.step", "Tapa.step"]), names
    assert all(Path(f).stat().st_size > 1000 for f in files)
    doc = App.openDocument(str(folder/"Caja.FCStd"))
    try:
        assert {"Base", "Tapa", "Placa", "Parametros"} <= {o.Name for o in doc.Objects}
        assert len(doc.getObject("Placa").Shape.Solids) == 176
        assert json.loads(doc.getObject("Parametros").ConfiguracionJSON)["name"] == "Caja ñandú"
        assert doc.getObject("Base").Shape.isValid()
    finally:
        App.closeDocument(doc.Name)
    before = {f: Path(f).stat().st_mtime_ns for f in files}
    try:
        cf.export_result(r, folder)
        raise AssertionError("sobrescribió")
    except cf.CajaError as exc:
        assert "ya contiene" in str(exc)
    assert before == {f: Path(f).stat().st_mtime_ns for f in files}
    assert cache.read_count == reads
    # Sin documentos abiertos de más.
    assert not [d for d in App.listDocuments() if d.startswith("CajaCircuito")], App.listDocuments()
    return f"{dt:.2f}s {names}; read_count sin cambios ({cache.read_count})"


def t_export_manual():
    r = cf.build(cm.config_from(dict(name="Manual ñ", export_stl=False, export_step=True)), cache=cf.BoardCache())
    files = cf.export_result(r, TMP/"manual ñ")
    names = sorted(Path(f).name for f in files)
    assert names == ["Base.step", "Caja.FCStd", "Tapa.step"], names
    assert r["board_key"].startswith("manual:") and r["board_key"] == cf.read_board(r["config"])[2]["board_key"]
    return f"{names} {r['board_key']}"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(f"FreeCAD {'.'.join(App.Version()[:3])}; STEP {'disponible' if HAVE_STEP else 'NO disponible'}; "
          f"tmp={TMP}", flush=True)
    run("una sola lectura en 6 builds", t_una_lectura, True)
    run("formas en caché intactas", t_formas_intactas, True)
    run("orientaciones Z→X→Y→Z sin releer + LRU", t_orientaciones, True)
    run("cambio en disco (mtime y tamaño) en ruta con acentos", t_cambio_en_disco, True)
    run("LRU de originales (3 archivos)", t_lru_originales, True)
    run("sin temporales por cálculo", t_sin_temporales, True)
    run("sync_document STEP", t_sync_step, True)
    run("sync_document manual", t_sync_manual)
    run("export_result desde caché", t_export_cache, True)
    run("export_result manual", t_export_manual)
    print("\nResumen")
    for name, status, _ in RESULTS:
        print(f"  {status:4}  {name}  ({TIMES.get(name, 0):.1f} s)")
    for k in ("build primera carga (con Part.read)", "build siguiente (media de 5, caché)",
              "export_result (solo exportar)"):
        if k in TIMES:
            print(f"  tiempo {k}: {TIMES[k]:.2f} s")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS) - len(fails)} OK/SKIP, {len(fails)} FAIL")
    if not fails:
        shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

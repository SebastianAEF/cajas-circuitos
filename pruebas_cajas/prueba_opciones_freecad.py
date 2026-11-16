"""Geometría de las opciones de abertura, unión, cierre y apoyo con el Python de FreeCAD:

    "C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_opciones_freecad.py

Formas de abertura, altura de la unión, «abrir hasta la unión» con lengüeta de cierre,
cierres (fricción, labio, imanes, apoyada), apoyos bajo el PCB, pisadores y detección
automática de aberturas. Usa la placa del usuario (solo lectura); si no existe, salta esas partes.
"""
import sys, time, copy, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cajas_freecad as CF
import cajas_modelo as M

ECDL = r"C:/Users/tupin/Documents/adn8834-ecdl/pcb/v2-revision-tierra/ECDL_ADN8834_MOD_GND.step"
cache = CF.BoardCache()
fallos = []


def check(nombre, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + nombre + ("  " + str(extra) if extra != "" else ""), flush=True)
    if not ok:
        fallos.append(nombre)


def construir(**kw):
    c = M.normalize(dict(copy.deepcopy(M.DEFAULTS), **kw))
    if c["step_path"]:
        _s, size, _i = CF.fit_board(*cache.get(c), c)
        c["board_x"], c["board_y"], c["board_z"] = size
    M.validate(c)
    t = time.time()
    r = CF.build(c, cache=cache)
    r["t"] = time.time() - t
    return r


def prueba(nombre, fn):
    try:
        fn()
    except Exception as ex:
        check(nombre, False, "".join(traceback.format_exception_only(type(ex), ex)).strip())


def formas():
    for forma in ("rectangular", "redondeada", "circular", "ovalada"):
        p = {"side": "frente", "u": 10, "v": 9, "width": 10, "height": 5, "shape": forma, "radius": 1.5}
        r = construir(ports=[p, dict(p, side="izquierda", u=-8), dict(p, side="tapa", u=0, v=0)])
        check(f"forma {forma}: base y tapa válidas", r["base"].isValid() and r["lid"].isValid(), round(r["t"], 2))
    # El volumen quitado por un círculo es menor que por un rectángulo del mismo ancho.
    vols = {}
    for forma in ("rectangular", "circular"):
        p = {"side": "frente", "u": 10, "v": 9, "width": 6, "height": 6, "shape": forma}
        vols[forma] = construir(ports=[p])["base"].Volume
    check("círculo quita menos material que el cuadrado", vols["circular"] > vols["rectangular"], vols)
prueba("formas", formas)


def union():
    r = construir(split_height=9.0)
    check("altura de unión 9 mm", abs(r["dimensions"]["seam"] - 9) < 1e-6 and r["base"].BoundBox.ZMax < 9 + 1.9,
          (r["dimensions"]["seam"], r["base"].BoundBox.ZMax))
    # Abertura circular centrada en la unión: se corta mitad en base, mitad en tapa.
    p = {"side": "frente", "u": 0, "v": 9, "width": 6, "height": 6, "shape": "circular"}
    r = construir(split_height=9.0, ports=[p], closure="labio")
    check("círculo cortado en la unión (labio)", r["base"].isValid() and r["lid"].isValid() and r["tongues"] == 4,
          r["warnings"])
prueba("union", union)


def hasta_union():
    p = {"side": "frente", "u": 0, "v": 7, "width": 6, "height": 6, "shape": "circular", "to_seam": True}
    r = construir(ports=[p])
    check("abrir hasta la unión con lengüeta de cierre en la tapa", r["fit"]["port_tabs"] == 1 and r["lid"].isValid(),
          r["fit"])
    # La tapa con su lengüeta llena la ranura sobre el círculo: base + tapa ~ sin hueco sobre z = 10+.
    seam = r["dimensions"]["seam"]
    probe = CF.Part.makeBox(4, r["config"]["wall"], seam - 11, CF.V(-2, -r["dimensions"]["outer_y"]/2, 11))
    lleno = probe.common(r["lid"]).Volume / probe.Volume
    check("la ranura queda cerrada por la tapa sobre la abertura", lleno > 0.95, round(lleno, 3))
prueba("hasta_union", hasta_union)


def cierres():
    for cierre in ("friccion", "labio", "imanes", "apoyada"):
        r = construir(closure=cierre)
        check(f"cierre {cierre}", r["base"].isValid() and r["lid"].isValid(),
              (r["tongues"], round(r["fit_interference"], 3), round(r["t"], 2)))
    r = construir(closure="imanes", magnet_diameter=5, magnet_height=2)
    bb = r["base"].BoundBox
    check("imanes: torres sobresalen en las esquinas", bb.XLength > r["dimensions"]["outer_x"] + 1,
          (bb.XLength, r["dimensions"]["outer_x"], r["fit"].get("magnet_protrusion")))
    # Alojamiento: un cilindro del tamaño del imán entra en la base sin tocarla.
    c = r["config"]
    seam = r["dimensions"]["seam"]
    delta = (c["magnet_diameter"]/2 + c["magnet_clearance"] + CF.MAGNET_WALL)/2**0.5
    x, y = r["dimensions"]["inner_x"]/2 + delta, r["dimensions"]["inner_y"]/2 + delta
    iman = CF.Part.makeCylinder(2.5, 2, CF.V(x, y, seam - 2))
    check("imán cabe en el alojamiento de la base", iman.common(r["base"]).Volume < 1e-6)
    iman_t = CF.Part.makeCylinder(2.5, 2, CF.V(x, y, seam))
    check("imán cabe en el alojamiento de la tapa", iman_t.common(r["lid"]).Volume < 1e-6)
prueba("cierres", cierres)


def apoyos():
    for modo in ("ninguno", "esquinas", "repisa"):
        r = construir(step_path=ECDL, margin=0, board_rest=modo, hold_down=True)
        check(f"ECDL apoyo {modo} + pisadores", r["base"].isValid() and r["lid"].isValid(),
              (r["fit"]["rests"], r["fit"]["hold_down_posts"], round(r["t"], 2),
               [w for w in r["warnings"] if "poyo" in w or "isador" in w]))
    # El PCB descansa sobre los apoyos: tocan su cara inferior (distancia 0).
    r = construir(step_path=ECDL, margin=0, board_rest="esquinas")
    frame = CF._pcb_frame(r["config"], r["board_info"], [r["config"][k] for k in ("board_x", "board_y", "board_z")])
    pcb = r["board"].Solids[r["board_info"]["pcb_index"]]
    dist = pcb.distToShape(r["base"])[0]
    check("PCB apoyado sobre la base (distancia 0)", dist < 1e-3, (round(dist, 4), round(frame[4], 3)))
    r0 = construir(step_path=ECDL, margin=0.5, board_rest="ninguno")
    d0 = r0["board"].Solids[r0["board_info"]["pcb_index"]].distToShape(r0["base"])[0]
    check("sin apoyos el PCB no toca la base", d0 > 0.1, round(d0, 3))
if Path(ECDL).is_file():
    prueba("apoyos", apoyos)
else:
    print("SKIP apoyos: no existe", ECDL)


def detectar():
    r = construir(step_path=ECDL, margin=0)
    t = time.time()
    det = CF.detect_ports(r, margin=0.5)
    ports = det["ports"]
    print("   detectadas:", ports, round(time.time() - t, 2), "s")
    check("detecta 5 aberturas (4 frente, 1 izquierda)", sorted(p["side"] for p in ports) ==
          ["frente"]*4 + ["izquierda"], [(p["side"], p["shape"]) for p in ports])
    check("cuerpo cuadrado en la pared -> rectangular redondeada",
          all(p["shape"] == "redondeada" for p in ports))
    # Un cilindro puro que atraviesa la pared sí se detecta como circular.
    falso = dict(r, board=CF.Part.makeCompound([CF.Part.makeCylinder(2.5, 10, CF.V(0, -40, 12), CF.V(0, 1, 0))]))
    pc = CF.detect_ports(falso, margin=0.5)["ports"]
    check("cilindro detectado como circular", len(pc) == 1 and pc[0]["shape"] == "circular"
          and abs(pc[0]["width"] - 6) < 0.02, pc)
    r2 = construir(step_path=ECDL, margin=0, ports=ports)
    check("con las aberturas detectadas nada atraviesa la caja", not r2["crossed_faces"], r2["crossed_faces"])
    # Insertar en vertical: aberturas abiertas hasta la unión, cerradas por la tapa.
    det2 = CF.detect_ports(r, margin=0.5, to_seam=True)
    r3 = construir(step_path=ECDL, margin=0, ports=det2["ports"])
    check("detectadas abiertas hasta la unión: válidas y sin cruces",
          not r3["crossed_faces"] and r3["fit"]["port_tabs"] >= 1, (r3["fit"]["port_tabs"], r3["crossed_faces"]))
    # La placa puede bajar en vertical dentro de la base: subirla no choca con la base.
    choque = 0
    for dz in (2, 5, 10, 20):
        b = r3["board"].translated(CF.V(0, 0, dz))
        choque += sum(s.common(r3["base"]).Volume for s in b.Solids if s.BoundBox.intersect(r3["base"].BoundBox))
    check("la placa entra en vertical en la base", choque < 1e-3, round(choque, 4))
    # Unión a la altura de los conectores + aberturas detectadas sin to_seam
    vs = sorted(p["v"] for p in ports)
    r4 = construir(step_path=ECDL, margin=0, split_height=vs[len(vs)//2], closure="labio")
    det4 = CF.detect_ports(r4, margin=0.5)
    r5 = construir(step_path=ECDL, margin=0, split_height=vs[len(vs)//2], closure="labio", ports=det4["ports"])
    check("unión a la altura de los conectores + labio", not r5["crossed_faces"] and r5["base"].isValid(),
          (round(vs[len(vs)//2], 2), r5["warnings"]))
if Path(ECDL).is_file():
    prueba("detectar", detectar)
else:
    print("SKIP detectar: no existe", ECDL)

print("FALLOS:", fallos)
sys.exit(1 if fallos else 0)

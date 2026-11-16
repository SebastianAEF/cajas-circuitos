"""Constructor geométrico de «Cajas para circuitos». Ejecutar con el Python de FreeCAD.

Todo lo que está antes del marcador del worker se incrusta tal cual en las macros
.FCMacro autónomas: solo usa la biblioteca estándar y FreeCAD/Part/MeshPart, y
funciona con o sin interfaz gráfica de FreeCAD. Todas las unidades son mm.

Coordenadas: origen XY en el centro de la caja, z = 0 en la base exterior.
Aberturas laterales: u = posición horizontal del centro a lo largo de la cara,
v = altura del centro desde z = 0. Aberturas de tapa: u = X, v = Y.
Soportes: x, y del centro; height desde la cara superior del piso.

Cierre «friccion»: lengüetas en el borde superior de la pared de la base (tramo
recto + bisel de entrada) que entran en ranuras de la falda de la tapa con
«clearance» de juego por lado. Unos nervios en el centro de cada lengüeta
sobresalen «rib_interference» por lado sobre ese juego (rampa + tramo recto
inferior) y dan la retención. El ajuste real depende de la impresora.
"""
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

try:
    import FreeCAD as App
    import Part
    V = App.Vector
    _IMPORT_ERROR = None
except Exception as _exc:  # el worker lo informa en la respuesta JSON
    App = Part = V = None
    _IMPORT_ERROR = _exc

MIN_STRIP = 0.8        # material mínimo (mm) que se acepta entre aberturas y bordes
MIN_SLOT_WALL = 0.8    # material mínimo a cada lado de una ranura de la tapa
EPS = 1e-6
LATERAL = ("frente", "fondo", "izquierda", "derecha")
# Medida de placa que dimensiona la caja con STEP: solo el PCB (por defecto), la
# envolvente de todos los componentes o medidas escritas por el usuario.
BOARD_FITS = ("pcb", "total", "personalizado")
PCB_MAX_THICKNESS = 5.0    # un PCB es un sólido plano de 0,2 a 5 mm de espesor
PCB_MIN_THICKNESS = 0.2    # descarta láminas de serigrafía o máscara
TIGHT_MARGIN = 0.2         # holgura lateral por debajo de la cual la placa queda justa
CORNER_FACTOR = 1 - 1/math.sqrt(2)
# Cierres: lengüetas cortas con nervios, labio continuo, imanes (con labio de
# alineación) o tapa apoyada.
CLOSURES = ("friccion", "labio", "imanes", "apoyada")
LIP_CLOSURES = ("friccion", "labio", "imanes")
MAGNET_WALL = 1.0          # material alrededor del alojamiento del imán
MAGNET_DEPTH_EXTRA = 0.2   # el alojamiento es 0,2 mm más hondo que el imán
MAGNET_FLOOR = 0.6         # material mínimo bajo el alojamiento
RIB_SPACING = 25.0         # separación de nervios en el labio continuo
PORT_SHAPES = ("rectangular", "redondeada", "circular", "ovalada")
BOARD_RESTS = ("ninguno", "esquinas", "repisa")


def inner_corner_radius(c):
    """Radio de las esquinas interiores. Nunca entra en la envolvente de la placa:
    con poca holgura lateral se reduce (con holgura 0 la esquina interior es viva)."""
    return max(0.0, min(c["radius"] - c["wall"], c["margin"]/CORNER_FACTOR))


def corner_wall(c):
    """Espesor de pared en la diagonal de la esquina."""
    r, w, ri = c["radius"], c["wall"], inner_corner_radius(c)
    return min(w, math.sqrt(2)*(w + ri - r) + r - ri)


class CajaError(ValueError):
    """Error de configuración o de geometría explicable al usuario (en español)."""


def _f(x):
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# ---------------------------------------------------------------------------
# Placa
# ---------------------------------------------------------------------------

def _read_step(path, stats=None):
    """Part.read del STEP. `stats` (dict opcional) cuenta lecturas reales y copias temporales."""
    stats = stats if stats is not None else {}
    p = Path(path)
    if not p.is_file():
        raise CajaError("No se encuentra la placa STEP: " + str(path))
    stats["reads"] = stats.get("reads", 0) + 1
    try:
        return Part.read(str(p))
    except Exception as first:
        # OCC suele leer rutas Unicode; si falla, se reintenta desde una copia ASCII.
        tmpdir = tempfile.mkdtemp(prefix="cajas_step_")
        stats["temp_copies"] = stats.get("temp_copies", 0) + 1
        try:
            tmp = os.path.join(tmpdir, "placa" + (p.suffix.lower() or ".step"))
            shutil.copyfile(str(p), tmp)
            stats["reads"] += 1
            try:
                return Part.read(tmp)
            except Exception:
                raise CajaError("FreeCAD no pudo leer el STEP «" + p.name + "»: " + str(first)) from None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _bound_box(shape):
    """Caja envolvente ajustada. BoundBox de OCC puede ser muy holgada en B-splines.

    Se calcula sobre la geometría exacta (useTriangulation=False): con la GUI, una forma
    mostrada en el visor ya tiene una triangulación gruesa y la envolvente basada en ella
    puede quedar más chica o más grande que la real."""
    try:
        b = shape.optimalBoundingBox(False, False)
        if b.isValid() and b.XLength > 0:
            return b, "optima"
    except Exception:
        pass
    return shape.BoundBox, "aproximada"


def _up_axis(config):
    axis = str(config.get("up_axis", "Z")).strip().upper()
    if axis not in ("X", "Y", "Z"):
        raise CajaError("El eje vertical debe ser X, Y o Z.")
    return axis


def _rotation(config):
    rot = _num(config, "rotation") % 360.0
    return 0.0 if abs(rot - 360.0) < 1e-12 else rot


def _step_file_key(path):
    """(ruta normalizada, tamaño, mtime_ns) del STEP. Cambia si el archivo cambia en disco."""
    full = os.path.abspath(str(path))
    try:
        st = os.stat(full)
    except OSError:
        raise CajaError("No se encuentra la placa STEP: " + str(path)) from None
    if not os.path.isfile(full):
        raise CajaError("No se encuentra la placa STEP: " + str(path))
    return (os.path.normcase(full), st.st_size, st.st_mtime_ns)


def _read_step_colored(path, stats=None):
    """Lee el STEP con ImportGui para conservar los colores por cara (los que exporta
    KiCad). Solo funciona con la GUI de FreeCAD. Devuelve (forma, colores, grupos): un color
    (r, g, b, a) por cara de la forma, en el mismo orden, y por cada sólido (grupo, nombre)
    del componente del ensamblaje al que pertenece; o None si no se puede (y entonces se usa
    Part.read). El documento temporal de la lectura se cierra siempre."""
    if not App.GuiUp:
        return None
    try:
        import ImportGui
    except Exception:
        return None
    p = Path(path)
    if not p.is_file():
        raise CajaError("No se encuentra la placa STEP: " + str(path))
    stats = stats if stats is not None else {}
    doc = None
    try:
        doc = _new_document("CajasLecturaStep", hidden=True)
        stats["reads"] = stats.get("reads", 0) + 1
        ImportGui.insert(str(p), doc.Name)
        shapes, colors, groups = [], [], []
        for o in doc.Objects:
            if o.TypeId != "Part::Feature" or o.Shape.isNull() or not o.Shape.Faces:
                continue
            # Ubicación global: la de la pieza compuesta con la de sus contenedores (App::Part)
            # del ensamblaje. Part.getShape(transform=True) aplica solo la propia.
            s = o.Shape.copy()
            s.Placement = o.getGlobalPlacement()
            vo = _view_object(o)
            dc = [tuple(c) for c in getattr(vo, "DiffuseColor", [])] if vo is not None else []
            if len(dc) != len(s.Faces):
                sc = tuple(getattr(vo, "ShapeColor", ())) if vo is not None else ()
                dc = [sc if sc else None]*len(s.Faces)
            shapes.append(s)
            colors.extend(dc)
            groups.extend([(len(shapes) - 1, str(o.Label))]*len(s.Solids))
        if not shapes:
            return None
        shape = Part.makeCompound(shapes)
        if len(shape.Solids) != len(groups):
            groups = None
        if len(shape.Faces) != len(colors) or all(c is None for c in colors):
            return shape, None, groups
        return shape, [c if c is not None else COLOR_PLACA + (0.0,) for c in colors], groups
    except CajaError:
        raise
    except Exception:
        return None
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass


def _load_original(path, stats=None, colors=False):
    """Lee el STEP, lo repara si hace falta y lo valida. Devuelve (forma, info) sin orientar.

    Con colors=True (y la GUI de FreeCAD) conserva los colores por cara del STEP en
    info["face_colors"], alineados con shape.Faces."""
    read = _read_step_colored(path, stats) if colors else None
    shape, face_colors, groups = read if read is not None else (_read_step(path, stats), None, None)
    info = {"warnings": [], "source": "step", "face_colors": face_colors, "solid_groups": groups}
    if shape.isNull() or not shape.Vertexes:
        raise CajaError("El STEP no contiene geometría utilizable.")
    if not shape.isValid():
        fixed = shape.copy()
        try:
            fixed.fix(1e-4, 1e-5, 1e-3)
        except Exception:
            pass
        if fixed.isValid():
            shape = fixed
            info["face_colors"] = None    # la reparación puede cambiar el orden de las caras
            info["solid_groups"] = None
            info["warnings"].append("El STEP tenía geometría no válida y se reparó automáticamente.")
        else:
            info["warnings"].append("El STEP tiene geometría no válida según FreeCAD; se usa solo como "
                                    "referencia y la detección de colisiones puede ser imprecisa.")
    if not shape.Solids:
        info["warnings"].append("El STEP no contiene sólidos (solo superficies): las colisiones con "
                                "soportes se estiman con cajas envolventes.")
    return shape, info


def _normalize_board(shape, info):
    """Centra XY y pone ZMin = 0 (modifica `shape`, que debe ser propia). Devuelve (forma, size, info)."""
    b, method = _bound_box(shape)
    if min(b.XLength, b.YLength, b.ZLength) <= 1e-5:
        raise CajaError("La placa debe tener volumen y espesor en sus tres ejes.")
    size = [b.XLength, b.YLength, b.ZLength]
    shape.translate(V(-(b.XMin + b.XMax)/2, -(b.YMin + b.YMax)/2, -b.ZMin))
    info["bbox_method"] = method
    if method != "optima":
        info["warnings"].append("No se pudo calcular la envolvente ajustada del STEP; se usa la aproximada "
                                "(puede sobredimensionar la caja).")
    return shape, size, info


def _orient_original(original, info, axis, rot):
    """Orienta y normaliza una COPIA del original (nunca lo modifica).

    La forma devuelta es un compuesto con ubicación identidad que contiene la placa
    orientada: así un Part::Feature puede colocarla con Placement sin perder la
    orientación (asignar Placement reemplaza la ubicación propia de la forma).
    """
    shape = original.copy()
    if axis == "X":
        shape.rotate(V(), V(0, 1, 0), -90)   # +X del STEP pasa a +Z
    elif axis == "Y":
        shape.rotate(V(), V(1, 0, 0), 90)    # +Y del STEP pasa a +Z
    if rot % 360:
        shape.rotate(V(), V(0, 0, 1), rot)
    info = {"warnings": list(info["warnings"]), "source": info["source"],
            "face_colors": info.get("face_colors"),   # rotar no cambia el orden de las caras
            "solid_groups": info.get("solid_groups")}
    shape, size, info = _normalize_board(shape, info)
    info["pcb"], info["pcb_index"] = _detect_pcb(shape, size)
    # Envolventes ajustadas de cada sólido (orientado), medidas solo cuando hacen falta y
    # compartidas por todas las copias de esta orientación en la caché.
    info["solid_bbs"] = {}
    return Part.makeCompound([shape]), size, info


def _detect_pcb(shape, size):
    """(envolvente [x0, y0, z0, x1, y1, z1], índice del sólido) del PCB desnudo en la forma ya
    normalizada, o (None, None).

    El PCB es el sólido plano (espesor entre PCB_MIN y PCB_MAX_THICKNESS) de mayor
    área en planta, que ocupe al menos un cuarto de la planta total. La BoundBox
    rápida (siempre ≥ la real) solo preselecciona; se mide con la envolvente ajustada.
    """
    best = None
    for i, s in enumerate(shape.Solids):
        b = s.BoundBox
        if b.XLength*b.YLength < 0.25*size[0]*size[1]:
            continue
        o, _ = _bound_box(s)
        if not PCB_MIN_THICKNESS <= o.ZLength <= PCB_MAX_THICKNESS:
            continue
        if o.ZLength*4 > min(o.XLength, o.YLength):
            continue
        key = (round(o.XLength*o.YLength, 3), o.ZLength)
        if best is None or key > best[0]:
            best = (key, [o.XMin, o.YMin, o.ZMin, o.XMax, o.YMax, o.ZMax], i)
    if best is None or best[0][0] < 0.25*size[0]*size[1]:
        return None, None
    return best[1], best[2]


def _manual_board(config):
    try:
        bx, by, bz = (_num(config, k) for k in ("board_x", "board_y", "board_z"))
    except (KeyError, TypeError, ValueError):
        raise CajaError("Faltan las medidas de la placa (board_x, board_y, board_z).") from None
    if min(bx, by, bz) <= 0:
        raise CajaError("La placa debe tener volumen y espesor en sus tres ejes.")
    shape, size, info = _normalize_board(Part.makeBox(bx, by, bz), {"warnings": [], "source": "manual"})
    info["board_key"] = "manual:" + ",".join(repr(v) for v in (bx, by, bz))
    return shape, size, info


def board_fit(c):
    fit = str(c.get("board_fit") or "pcb").strip().lower()
    if fit not in BOARD_FITS:
        raise CajaError("Tamaño de placa no reconocido (usa pcb, total o personalizado).")
    return fit


def fit_board(shape, size, info, config):
    """Elige la medida de placa que dimensiona la caja y centra la placa en ella.

    Devuelve (forma, [x, y, z], info). Con STEP:
    - "pcb": planta del PCB desnudo (los componentes que sobresalen, como conectores
      laterales, pueden atravesar la pared y piden una abertura); alto = total.
    - "total": envolvente de todos los componentes.
    - "personalizado": board_x/board_y/board_z de la configuración, centrado en el PCB.
    info["board_bbox"] es la envolvente real de la placa ya centrada,
    info["sizes"] las medidas total y del PCB, info["pcb_z"] la cara inferior del PCB.
    En modo manual (sin STEP) devuelve la placa tal cual.
    """
    info = dict(info, warnings=list(info.get("warnings", [])))
    pcb = info.get("pcb")
    info["sizes"] = {"total": list(size),
                     "pcb": [pcb[3] - pcb[0], pcb[4] - pcb[1], pcb[5] - pcb[2]] if pcb else None}
    info["pcb_z"] = pcb[2] if pcb else None
    bb = [-size[0]/2, -size[1]/2, 0.0, size[0]/2, size[1]/2, size[2]]
    if info.get("source") != "step":
        info["fit"] = "manual"
        info["shift"] = (0.0, 0.0)
        info["board_bbox"] = bb
        return shape, list(size), info
    fit = board_fit(config)
    cx = cy = 0.0
    if fit != "total":
        if pcb:
            cx, cy = (pcb[0] + pcb[3])/2, (pcb[1] + pcb[4])/2
        elif fit == "pcb":
            info["warnings"].append("No se identificó el PCB en el STEP (ningún sólido plano grande): "
                                    "se usa la envolvente de todos los componentes.")
    if fit == "personalizado":
        try:
            fsize = [_num(config, k) for k in ("board_x", "board_y", "board_z")]
        except CajaError:
            raise CajaError("Faltan las medidas personalizadas de la placa (ancho, largo y alto).") from None
        if min(fsize) <= 0:
            raise CajaError("Las medidas personalizadas de la placa deben ser mayores que cero.")
    elif fit == "pcb" and pcb:
        fsize = [pcb[3] - pcb[0], pcb[4] - pcb[1], size[2]]
    else:
        fsize = list(size)
    if abs(cx) > 1e-9 or abs(cy) > 1e-9:
        shape = shape.translated(V(-cx, -cy, 0))
        info["board_key"] = f"{info['board_key']}|centro:{-cx!r},{-cy!r}"
    info["fit"] = fit
    info["shift"] = (-cx, -cy)
    info["board_bbox"] = [bb[0] - cx, bb[1] - cy, bb[2], bb[3] - cx, bb[4] - cy, bb[5]]
    return shape, fsize, info


def _orientation_key_str(key):
    (path, fsize, mtime), axis, rot = key
    return f"step:{path}|{fsize}|{mtime}|{axis}|{rot!r}"


def read_board(config):
    """Lee y orienta la placa (sin caché). Devuelve (forma, [x, y, z], info).

    La forma queda con XY centrado y ZMin = 0. Solo usa step_path, up_axis,
    rotation y board_x/board_y/board_z (modo manual). info["board_key"]
    identifica la placa (mismo formato que BoardCache).
    """
    path = str(config.get("step_path") or "").strip()
    if not path:
        return _manual_board(config)
    axis, rot = _up_axis(config), _rotation(config)
    fkey = _step_file_key(path)
    original, info = _load_original(path)
    shape, size, info = _orient_original(original, info, axis, rot)
    info["board_key"] = _orientation_key_str((fkey, axis, round(rot, 6)))
    return shape, size, info


class BoardCache:
    """Caché LRU de placas STEP para una sesión de FreeCAD de larga duración.

    - Originales: forma de Part.read ya reparada/validada. Clave
      (ruta normcase absoluta, st_size, st_mtime_ns): si el archivo cambia en
      disco, la clave cambia, se descartan las entradas viejas de esa ruta y se relee.
    - Orientaciones: placa orientada y normalizada (XY centrado, ZMin = 0) con el
      size de optimalBoundingBox. Clave (clave_original, up_axis, round(rot % 360, 6)).
    Al expulsar un original se expulsan sus orientaciones. La orientación se
    calcula siempre sobre original.copy(); las formas en caché no se mutan nunca.
    get() devuelve un envoltorio nuevo (comparte la topología, no la ubicación):
    el llamador puede trasladarlo o rotarlo sin afectar la caché, pero no debe
    usar operaciones que modifiquen la geometría en sitio (fix, sewShape...).
    El modo manual (step_path vacío) no se cachea.
    """

    def __init__(self, max_originals=2, max_oriented=4, colors=False):
        from collections import OrderedDict
        import threading
        self.max_originals = max(1, int(max_originals))
        self.max_oriented = max(1, int(max_oriented))
        self._originals = OrderedDict()   # clave_original -> (forma, info)
        self._oriented = OrderedDict()    # clave_orientación -> (forma, size, info)
        self._lock = threading.RLock()
        self._io = {"reads": 0, "temp_copies": 0}
        self.colors = colors              # leer con ImportGui y conservar colores (solo con GUI)
        self.orient_count = 0
        self.hits = 0

    @property
    def read_count(self):
        return self._io["reads"]

    @property
    def temp_copies(self):
        return self._io["temp_copies"]

    def stats(self):
        with self._lock:
            return {"read_count": self.read_count, "orient_count": self.orient_count, "hits": self.hits,
                    "temp_copies": self.temp_copies, "originals": len(self._originals),
                    "oriented": len(self._oriented)}

    def clear(self):
        with self._lock:
            self._originals.clear()
            self._oriented.clear()

    def _drop_original(self, key):
        self._originals.pop(key, None)
        for k in [k for k in self._oriented if k[0] == key]:
            del self._oriented[k]

    def _original(self, path):
        fkey = _step_file_key(path)
        entry = self._originals.get(fkey)
        if entry is not None:
            self._originals.move_to_end(fkey)
            return fkey, entry
        for old in [k for k in self._originals if k[0] == fkey[0]]:
            self._drop_original(old)          # el archivo cambió en disco
        entry = _load_original(path, self._io, self.colors)
        self._originals[fkey] = entry
        while len(self._originals) > self.max_originals:
            self._drop_original(next(iter(self._originals)))
        return fkey, entry

    def get(self, config):
        """(forma, size, info) como read_board(config), usando la caché."""
        path = str(config.get("step_path") or "").strip()
        if not path:
            return _manual_board(config)
        axis, rot = _up_axis(config), _rotation(config)
        with self._lock:
            fkey, (original, oinfo) = self._original(path)
            okey = (fkey, axis, round(rot, 6))
            entry = self._oriented.get(okey)
            if entry is not None:
                self._oriented.move_to_end(okey)
                self.hits += 1
            else:
                entry = _orient_original(original, oinfo, axis, rot)
                entry[2]["board_key"] = _orientation_key_str(okey)
                self.orient_count += 1
                self._oriented[okey] = entry
                while len(self._oriented) > self.max_oriented:
                    self._oriented.popitem(last=False)
            shape, size, info = entry
            info = dict(info, warnings=list(info["warnings"]))
            return shape.translated(V()), list(size), info


# ---------------------------------------------------------------------------
# Primitivas
# ---------------------------------------------------------------------------

def rounded_box(x, y, z, radius, z0=0):
    shape = Part.makeBox(x, y, z, V(-x/2, -y/2, z0))
    if radius > 1e-4:
        edges = [e for e in shape.Edges if e.BoundBox.ZLength > z - 1e-5]
        shape = shape.makeFillet(radius, edges)
    return shape


def profile_wire(rect, z):
    x0, y0, x1, y1 = rect
    return Part.makePolygon([V(x0, y0, z), V(x1, y0, z), V(x1, y1, z), V(x0, y1, z), V(x0, y0, z)])


def ruled_solid(sections):
    """Sólido reglado por secciones rectangulares [(rect, z), ...]."""
    return Part.makeLoft([profile_wire(r, z) for r, z in sections], True, True)


def _shrink(rect, dx, dy):
    x0, y0, x1, y1 = rect
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _box_bb(shape_or_tuple):
    if isinstance(shape_or_tuple, tuple):
        return shape_or_tuple
    b = shape_or_tuple.BoundBox
    return (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)


def _bb_overlap(a, b, grow=0.0):
    a, b = _box_bb(a), _box_bb(b)
    return all(a[i] - grow < b[i + 3] and b[i] - grow < a[i + 3] for i in range(3))


def _profile_prism(shape, w, h, r, length):
    """Prisma de sección w × h centrada en el origen del plano XY, de z = 0 a `length`."""
    if shape == "circular":
        return Part.makeCylinder(w/2, length)
    if shape == "ovalada" and abs(w - h) > 1e-6:
        rr = min(w, h)/2
        if w > h:
            core = Part.makeBox(w - 2*rr, h, length, V(-w/2 + rr, -h/2, 0))
            ends = [Part.makeCylinder(rr, length, V(s*(w/2 - rr), 0, 0)) for s in (-1, 1)]
        else:
            core = Part.makeBox(w, h - 2*rr, length, V(-w/2, -h/2 + rr, 0))
            ends = [Part.makeCylinder(rr, length, V(0, s*(h/2 - rr), 0)) for s in (-1, 1)]
        return core.multiFuse(ends).removeSplitter()
    if shape == "ovalada":
        return Part.makeCylinder(w/2, length)
    box = Part.makeBox(w, h, length, V(-w/2, -h/2, 0))
    if shape == "redondeada":
        rr = min(r, min(w, h)/2 - 0.01)
        if rr > 0.01:
            box = box.makeFillet(rr, [e for e in box.Edges if e.BoundBox.ZLength > length - 1e-5])
    return box


def _place_lateral(local, side, u, v, d, s0, length):
    """Coloca un sólido local (x = u, y = v relativos, z = profundidad 0..length) en una cara
    lateral, ocupando desde s0 hasta s0 + length medidos hacia dentro desde la cara exterior."""
    ox, oy = d["outer_x"], d["outer_y"]
    shape = local.copy()
    shape.rotate(V(), V(1, 0, 0), 90)            # (x, y, z) -> (x, -z, y)
    if side == "frente":
        shape.translate(V(u, -oy/2 + s0 + length, v))
    elif side == "fondo":
        shape.translate(V(u, oy/2 - s0, v))
    else:
        shape.rotate(V(), V(0, 0, 1), 90)        # (x, -z, y) -> (z, x, y)
        x0 = -ox/2 + s0 if side == "izquierda" else ox/2 - s0 - length
        shape.translate(V(x0, u, v))
    return shape


def port_rect(p, seam):
    """(u0, v0, u1, v1) que ocupa la abertura en su cara (incluye la ranura hasta la unión)."""
    u, v, w, h = p["u"], p["v"], p["width"], p["height"]
    top = v + h/2
    if p.get("to_seam") and p["side"] != "tapa" and v - h/2 < seam - EPS:
        top = max(top, seam)
    return (u - w/2, v - h/2, u + w/2, top)


def port_cutter(p, d, wall):
    """Sólido que corta la abertura. Las laterales atraviesan la pared y 0,5 mm más.
    Con to_seam la abertura sigue hacia arriba, con su ancho, hasta el plano de unión."""
    side, u, v, w, h = p["side"], p["u"], p["v"], p["width"], p["height"]
    shape, r = p.get("shape", "rectangular"), p.get("radius", 0.0)
    if side == "tapa":
        z0 = d["ceiling"] - 0.5
        prism = _profile_prism(shape, w, h, r, d["outer_z"] + 1 - z0)
        prism.translate(V(u, v, z0))
        return prism
    if side not in LATERAL:
        raise CajaError("Cara de abertura no reconocida: " + str(side))
    depth = wall + 1.5
    local = _profile_prism(shape, w, h, r, depth)
    seam = d["seam"]
    if p.get("to_seam") and v - h/2 < seam - EPS and v + h/2 < seam:
        local = local.fuse(Part.makeBox(w, seam - v, depth, V(-w/2, 0, 0))).removeSplitter()
    return _place_lateral(local, side, u, v, d, -1.0, depth)


def port_lid_tab(p, d, wall, clearance):
    """Lengüeta de la tapa que baja por la ranura de una abertura «hasta la unión» y la
    cierra sobre el componente (su borde inferior sigue la forma de la abertura). None
    si no corresponde."""
    side, u, v, w, h = p["side"], p["u"], p["v"], p["width"], p["height"]
    seam = d["seam"]
    if side == "tapa" or not p.get("to_seam") or v + h/2 >= seam or v - h/2 >= seam:
        return None
    cl = max(0.1, clearance)
    z_low = v + 0.35*h                  # sin puntas finas junto a una abertura curva
    if seam - z_low < 0.8 or w - 2*cl < 1.0:
        return None
    tab = Part.makeBox(w - 2*cl, seam - z_low, wall, V(-w/2 + cl, z_low - v, 0))
    hole = _profile_prism(p.get("shape", "rectangular"), w, h, p.get("radius", 0.0), wall + 2)
    hole.translate(V(0, 0, -1))
    tab = tab.cut(hole).removeSplitter()
    if not tab.Solids:
        return None
    return _place_lateral(tab, side, u, v, d, 0.0, wall)


# ---------------------------------------------------------------------------
# Validación (duplicada de la aplicación: las macros pueden editarse a mano)
# ---------------------------------------------------------------------------

def validate_build(c, d):
    """Valida la configuración ya con las medidas reales de la placa. Devuelve advertencias."""
    warnings = []
    for key, label in (("wall", "La pared"), ("floor", "El piso"),
                       ("lid", "El techo de la tapa"), ("lid_depth", "La profundidad de tapa")):
        if c[key] <= 0:
            raise CajaError(label + " debe ser mayor que cero.")
    for key, label in (("margin", "La holgura lateral"), ("bottom_gap", "La holgura inferior"),
                       ("top_gap", "La holgura superior"),
                       ("radius", "El radio de esquina"), ("clearance", "El juego del encaje"),
                       ("rib_interference", "La interferencia de nervios")):
        if c[key] < 0:
            raise CajaError(label + " no puede ser negativa.")
    if c["margin"] < EPS:
        warnings.append("Holgura lateral 0: la placa queda ajustada contra las paredes (montaje a "
                        "presión). Según la tolerancia de la impresora puede entrar muy apretada.")
    elif c["margin"] < TIGHT_MARGIN:
        warnings.append(f"Holgura lateral de {_f(c['margin'])} mm: la placa queda muy justa.")
    if c["lid_depth"] >= d["inner_z"]:
        raise CajaError(f"La profundidad de tapa debe ser menor que la altura interior ({_f(d['inner_z'])} mm).")
    if corner_wall(c) < 0.75*c["wall"] - EPS:
        raise CajaError(f"El radio de esquina es demasiado grande para esta holgura lateral: la pared "
                        f"quedaría de {_f(corner_wall(c))} mm en las esquinas (mínimo {_f(0.75*c['wall'])} mm).")
    if c["radius"] >= min(d["outer_x"], d["outer_y"])/2:
        raise CajaError("El radio de esquina no cabe en la caja.")
    if c["closure"] not in CLOSURES:
        raise CajaError("Tipo de cierre no reconocido (usa " + ", ".join(CLOSURES) + ").")
    if c.get("split_height", 0) > 0:
        if d["seam"] < c["floor"] + 1 - EPS:
            raise CajaError(f"La altura de la unión debe ser al menos piso + 1 mm ({_f(c['floor'] + 1)} mm).")
        if d["seam"] > d["ceiling"] - 0.5 + EPS:
            raise CajaError(f"La altura de la unión debe quedar al menos 0,5 mm bajo el techo interior "
                            f"(máximo {_f(d['ceiling'] - 0.5)} mm).")
    if c["closure"] in LIP_CLOSURES:
        if c["wall"] < 3:
            raise CajaError("Los cierres con lengüeta o labio necesitan paredes de al menos 3 mm.")
        if not 1 <= c["tongue_height"] <= 4:
            raise CajaError("La altura de lengüeta debe estar entre 1 y 4 mm.")
        if c["lid_depth"] < c["tongue_height"] + 0.5:
            raise CajaError("La falda de la tapa (techo − unión) debe superar la lengüeta en al menos 0,5 mm: "
                            "baja la altura de la unión o la lengüeta.")
        if d["seam"] - c["floor"] < c["tongue_height"]:
            raise CajaError("La unión está demasiado baja para la lengüeta.")
        if c["clearance"] > 0.5:
            raise CajaError("El juego del encaje debe ser como máximo 0,5 mm por lado.")
        if c["rib_interference"] > 0.08:
            raise CajaError("La interferencia de nervios debe ser como máximo 0,08 mm por lado.")
        if c["wall"] < 2*MIN_SLOT_WALL + 0.8 + 2*c["clearance"] - EPS:
            raise CajaError(f"Con juego {_f(c['clearance'])} mm la pared debe medir al menos "
                            f"{_f(2*MIN_SLOT_WALL + 0.8 + 2*c['clearance'])} mm para dejar material junto a las ranuras.")
    if c["closure"] == "imanes":
        md, mh, mc = c["magnet_diameter"], c["magnet_height"], c["magnet_clearance"]
        if not 1 <= md <= 25:
            raise CajaError("El diámetro del imán debe estar entre 1 y 25 mm.")
        if not 0.5 <= mh <= 10:
            raise CajaError("El alto del imán debe estar entre 0,5 y 10 mm.")
        if not 0 <= mc <= 0.5:
            raise CajaError("La holgura del imán debe estar entre 0 y 0,5 mm.")
        pocket = mh + MAGNET_DEPTH_EXTRA + MAGNET_FLOOR
        if d["seam"] < pocket:
            raise CajaError(f"La unión está demasiado baja para alojar el imán (mínimo {_f(pocket)} mm).")
        if c["lid_depth"] + c["lid"] < pocket:
            raise CajaError("La tapa es demasiado baja para alojar el imán: sube el techo o baja la unión.")
    if c.get("board_rest", "ninguno") not in BOARD_RESTS:
        raise CajaError("Apoyo de placa no reconocido (usa " + ", ".join(BOARD_RESTS) + ").")
    if c.get("board_rest", "ninguno") != "ninguno" and not 0.5 <= c["rest_width"] <= 10:
        raise CajaError("El ancho del apoyo bajo la placa debe estar entre 0,5 y 10 mm.")
    ix, iy, ri = d["inner_x"]/2, d["inner_y"]/2, inner_corner_radius(c)
    for i, s in enumerate(c["supports"], 1):
        r, hole, h = s["diameter"]/2, s["hole"]/2, s["height"]
        if h <= 0 or r <= 0 or hole < 0 or hole >= r:
            raise CajaError(f"Soporte {i}: altura y diámetro positivos y agujero menor que el diámetro.")
        if h > d["seam"] - c["floor"] + EPS:
            raise CajaError(f"Soporte {i}: supera el plano de unión (máximo {_f(d['seam'] - c['floor'])} mm).")
        ax, ay = abs(s["x"]), abs(s["y"])
        out = ax + r > ix + EPS or ay + r > iy + EPS
        if not out and ri > 0:
            cx, cy = ix - ri, iy - ri
            out = ax > cx and ay > cy and math.hypot(ax - cx, ay - cy) + r > ri + EPS
        if out:
            raise CajaError(f"Soporte {i}: toca la pared o queda fuera del espacio interior.")
    seam, total, ceiling = d["seam"], d["outer_z"], d["ceiling"]
    rects = {}
    for i, p in enumerate(c["ports"], 1):
        side, u, v, w, h = p["side"], p["u"], p["v"], p["width"], p["height"]
        name = f"Abertura {i} ({side})"
        if side not in LATERAL + ("tapa",):
            raise CajaError(f"{name}: cara no reconocida.")
        if w <= 0 or h <= 0:
            raise CajaError(f"{name}: ancho y alto deben ser mayores que cero.")
        if p.get("shape", "rectangular") not in PORT_SHAPES:
            raise CajaError(f"{name}: forma no reconocida (usa " + ", ".join(PORT_SHAPES) + ").")
        if p.get("shape") == "redondeada" and p.get("radius", 0) < 0:
            raise CajaError(f"{name}: el radio de las esquinas no puede ser negativo.")
        if p.get("to_seam") and side != "tapa":
            if v - h/2 >= seam - EPS:
                raise CajaError(f"{name}: «abrir hasta la unión» necesita que la abertura empiece bajo "
                                f"la unión (z = {_f(seam)} mm).")
            h = port_rect(p, seam)[3] - (v - h/2)
            v = port_rect(p, seam)[3] - h/2
        if side == "tapa":
            if abs(u) + w/2 > ix + EPS or abs(v) + h/2 > iy + EPS:
                raise CajaError(f"{name}: debe quedar dentro del área interior de la tapa "
                                f"(±{_f(ix)} × ±{_f(iy)} mm) para no cortar la falda.")
            if min(ix - abs(u) - w/2, iy - abs(v) - h/2) < MIN_STRIP - EPS and \
                    min(ix - abs(u) - w/2, iy - abs(v) - h/2) > EPS:
                warnings.append(f"{name}: queda a menos de {_f(MIN_STRIP)} mm de la pared.")
        else:
            half = (d["outer_y"] if side in ("izquierda", "derecha") else d["outer_x"])/2
            inner_half = half - c["wall"]
            zb, zt, edge = v - h/2, v + h/2, abs(u) + w/2
            if edge > half + EPS:
                raise CajaError(f"{name}: sale del ancho de su cara.")
            if zb < c["floor"] - EPS:
                raise CajaError(f"{name}: invade el piso (borde inferior ≥ {_f(c['floor'])} mm).")
            if zt > total + EPS:
                raise CajaError(f"{name}: supera la altura de la caja ({_f(total)} mm).")
            strips = []
            if zt <= seam + EPS:
                strips.append(("costura (base)", seam - zt))
            if zb >= seam - EPS:
                strips.append(("costura (tapa)", zb - seam))
            strips.append(("techo", total - zt))
            strips.append(("borde lateral", half - edge))
            for what, t in strips:
                if EPS < t < MIN_STRIP - EPS:
                    raise CajaError(f"{name}: deja una tira de {_f(t)} mm junto a {what} "
                                    f"(mínimo {_f(MIN_STRIP)} mm). Muévela o hazla llegar al borde.")
            if edge > inner_half + EPS:
                warnings.append(f"{name}: invade la esquina y corta parte de la pared contigua.")
            if zb < seam - EPS < seam + EPS < zt:
                warnings.append(f"{name}: cruza la unión; se corta en la base y en la tapa.")
        rect = (u - w/2, v - h/2, u + w/2, v + h/2)
        for j, other in rects.get(side, []):
            gx = max(other[0] - rect[2], rect[0] - other[2])
            gy = max(other[1] - rect[3], rect[1] - other[3])
            sep = max(gx, gy)
            if EPS < sep < MIN_STRIP - EPS:
                raise CajaError(f"Aberturas {j} y {i} ({side}): el tabique entre ellas mide {_f(sep)} mm "
                                f"(mínimo {_f(MIN_STRIP)} mm).")
        rects.setdefault(side, []).append((i, rect))
    return warnings


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------

def _num(c, key, default=0.0):
    try:
        raw = c.get(key, default)
        if isinstance(raw, bool):
            raise ValueError("booleano")
        value = float(str(raw).strip().replace(",", "."))
        if not math.isfinite(value):
            raise ValueError("no finito")
        return value
    except (TypeError, ValueError):
        raise CajaError(f"El parámetro «{key}» debe ser un número.") from None


def _prepare(config):
    c = dict(config)
    for key in ("margin", "bottom_gap", "top_gap", "wall", "floor", "lid", "radius", "lid_depth",
                "tongue_height", "clearance", "rib_interference"):
        c[key] = _num(c, key)
    # Opciones posteriores: valores por defecto si la macro o el proyecto no las traen.
    for key, default in (("split_height", 0.0), ("rest_width", 2.0), ("magnet_diameter", 6.0),
                         ("magnet_height", 2.0), ("magnet_clearance", 0.15), ("pcb_offset", 0.0)):
        c[key] = _num(c, key, default)
    c["closure"] = str(c.get("closure", "friccion")).lower().replace("fricción", "friccion")
    c["board_rest"] = str(c.get("board_rest", "ninguno")).lower()
    c["hold_down"] = bool(c.get("hold_down", False))
    c["supports"] = [{k: _num(s, k) for k in ("x", "y", "height", "diameter", "hole")}
                     for s in (c.get("supports") or [])]
    ports = []
    for p in (c.get("ports") or []):
        q = {k: _num(p, k) for k in ("u", "v", "width", "height")}
        q["side"] = str(p.get("side", "")).lower()
        q["shape"] = str(p.get("shape", "rectangular")).lower()
        q["radius"] = _num(p, "radius", 1.0)
        q["to_seam"] = bool(p.get("to_seam", False))
        if q["shape"] == "circular":
            q["height"] = q["width"]
        ports.append(q)
    c["ports"] = ports
    return c


def _pcb_frame(c, info, size):
    """(x0, y0, x1, y1, z_inferior, z_superior) del PCB con la placa ya colocada en la caja
    (z absoluta), o None si no se conoce (STEP sin PCB detectado)."""
    z0 = c["floor"] + c["bottom_gap"]
    pcb = info.get("pcb")
    if pcb:
        sx, sy = info.get("shift", (0.0, 0.0))
        return (pcb[0] + sx, pcb[1] + sy, pcb[3] + sx, pcb[4] + sy, z0 + pcb[2], z0 + pcb[5])
    if info.get("source") != "step":
        # Medidas manuales: la envolvente es todo lo que se sabe; se apoya su base.
        return (-size[0]/2, -size[1]/2, size[0]/2, size[1]/2, z0, z0 + size[2])
    return None


def _solid_bbs(board, info, c):
    """Envolvente ajustada (x0, y0, z0, x1, y1, z1), ya colocada en la caja, de cada sólido
    de la placa. Usa la caché de la orientación: cada sólido se mide una sola vez (la
    BoundBox rápida de un STEP con B-splines puede ser varias veces mayor que la real)."""
    cache = info.get("solid_bbs")
    if cache is None:
        cache = {}
    sx, sy = info.get("shift", (0.0, 0.0))
    z0 = c["floor"] + c["bottom_gap"]
    out = []
    for i, s in enumerate(board.Solids):
        if i not in cache:
            o, _ = _bound_box(s)
            cache[i] = (o.XMin - sx, o.YMin - sy, o.ZMin - z0, o.XMax - sx, o.YMax - sy, o.ZMax - z0)
        b = cache[i]
        out.append((b[0] + sx, b[1] + sy, b[2] + z0, b[3] + sx, b[4] + sy, b[5] + z0))
    return out


def _components(bbs, info):
    """Envolventes de los sólidos de la placa que no son el PCB (en modo manual, ninguno)."""
    if info.get("source") != "step":
        return []
    idx = info.get("pcb_index")
    return [b for i, b in enumerate(bbs) if i != idx]


def _carve_components(piece, comps, grow=0.3):
    """Quita de `piece` la envolvente ajustada (ampliada `grow`) de cada componente que la toca."""
    pb = _box_bb(piece)
    boxes = []
    for o in comps:
        ob = (o[0] - grow, o[1] - grow, o[2] - grow, o[3] + grow, o[4] + grow, o[5] + grow)
        if not _bb_overlap(pb, ob, 0.0):
            continue
        box = Part.makeBox(ob[3] - ob[0], ob[4] - ob[1], ob[5] - ob[2], V(ob[0], ob[1], ob[2]))
        if _volume(piece.common(box)) > 1e-6:
            boxes.append(box)
    if boxes:
        piece = piece.cut(Part.makeCompound(boxes)).removeSplitter()
    return piece


def board_rests(c, d, frame, comps):
    """Apoyos de la base bajo el PCB («esquinas» o «repisa»): suben desde el piso hasta la
    cara inferior del PCB, pegados a la pared, y se recortan donde hay componentes debajo.
    Devuelve (lista de sólidos, avisos)."""
    mode, notes = c["board_rest"], []
    if mode == "ninguno":
        return [], notes
    if frame is None:
        return [], ["Apoyos de la placa omitidos: no se identificó el PCB en el STEP."]
    x0, y0, x1, y1, zb, _ = frame
    if zb - c["floor"] < 0.3:
        return [], notes            # el PCB ya descansa (casi) sobre el piso
    if zb > d["seam"] + EPS:
        return [], ["Apoyos de la placa omitidos: la cara inferior del PCB queda sobre la unión."]
    rw, ix, iy, ri = c["rest_width"], d["inner_x"], d["inner_y"], inner_corner_radius(c)
    anchor = min(0.2, c["floor"]/2)
    h = zb - c["floor"] + anchor
    cavity = rounded_box(ix, iy, h, ri, c["floor"] - anchor)
    if mode == "repisa":
        hole_w, hole_h = (x1 - x0) - 2*rw, (y1 - y0) - 2*rw
        piece = cavity
        if hole_w > 0.5 and hole_h > 0.5:
            piece = cavity.cut(Part.makeBox(hole_w, hole_h, h + 2, V(x0 + rw, y0 + rw, c["floor"] - anchor - 1)))
        pieces = [piece]
    else:
        L = max(6.0, 3*rw)
        pieces = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                X, Y = (x1 if sx > 0 else x0), (y1 if sy > 0 else y0)
                wx, wy = sx*ix/2, sy*iy/2
                boxes = []
                for ax, ay in ((L, rw), (rw, L)):
                    xa, xb = sorted((X - sx*ax, wx))
                    ya, yb = sorted((Y - sy*ay, wy))
                    if xb - xa > 0.1 and yb - ya > 0.1:
                        boxes.append(Part.makeBox(xb - xa, yb - ya, h, V(xa, ya, c["floor"] - anchor)))
                if boxes:
                    piece = boxes[0].fuse(boxes[1:]) if len(boxes) > 1 else boxes[0]
                    pieces.append(piece.common(cavity).removeSplitter())
    out, carved = [], 0
    for piece in pieces:
        before = _volume(piece)
        piece = _carve_components(piece, comps)
        if _volume(piece) < 0.9*before:
            carved += 1
        if _volume(piece) > 0.5:
            out.append(piece)
    if carved:
        notes.append(f"Apoyos de la placa recortados donde hay componentes bajo el PCB ({carved} de {len(pieces)}).")
    if not out:
        notes.append("Apoyos de la placa omitidos: los componentes bajo el PCB ocupan esas zonas.")
    return out, notes


def hold_down_posts(c, d, frame, comps):
    """Pisadores: postes que bajan del techo de la tapa hasta la cara superior del PCB, en
    sus esquinas, recortados donde hay componentes. Devuelve (sólidos, avisos)."""
    if not c["hold_down"]:
        return [], []
    if frame is None:
        return [], ["Pisadores omitidos: no se identificó el PCB en el STEP."]
    x0, y0, x1, y1, _, zt = frame
    ceiling = d["ceiling"]
    if ceiling - zt < 0.3:
        return [], []               # el techo ya toca el PCB
    s = max(3.0, 1.5*c["rest_width"])
    out, omitted = [], 0
    ix, iy = d["inner_x"]/2, d["inner_y"]/2
    for sx in (-1, 1):
        for sy in (-1, 1):
            X, Y = (x1 if sx > 0 else x0), (y1 if sy > 0 else y0)
            xa, xb = sorted((X, X - sx*s))
            ya, yb = sorted((Y, Y - sy*s))
            xa, xb, ya, yb = max(xa, -ix), min(xb, ix), max(ya, -iy), min(yb, iy)
            if xb - xa < 1 or yb - ya < 1:
                omitted += 1
                continue
            post = Part.makeBox(xb - xa, yb - ya, ceiling + 0.2 - zt, V(xa, ya, zt))
            before = _volume(post)
            post = _carve_components(post, comps)
            # Un poste que perdió parte de su sección ya no apoya bien en el PCB.
            if _volume(post) < 0.9*before or not post.Solids:
                omitted += 1
                continue
            out.append(post)
    notes = []
    if omitted:
        notes.append(f"Pisadores omitidos en {omitted} esquina(s) por componentes junto a ellas.")
    return out, notes


def magnet_towers(c, d):
    """Torres de esquina con alojamiento para imanes cilíndricos.

    Cada torre queda fuera de la cavidad (sobresale de la esquina exterior) para no
    quitar espacio a la placa. Devuelve (torres_base, torres_tapa, huecos_base,
    huecos_tapa, saliente) con saliente = cuánto sobresalen de la caja en X e Y."""
    md, mh, mc = c["magnet_diameter"], c["magnet_height"], c["magnet_clearance"]
    rp = md/2 + mc
    rt = rp + MAGNET_WALL
    delta = rt/math.sqrt(2)
    seam, total = d["seam"], d["outer_z"]
    depth = mh + MAGNET_DEPTH_EXTRA
    tb, tl, hb, hl = [], [], [], []
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx*(d["inner_x"]/2 + delta), sy*(d["inner_y"]/2 + delta)
            tb.append(Part.makeCylinder(rt, seam, V(x, y, 0)))
            tl.append(Part.makeCylinder(rt, total - seam, V(x, y, seam)))
            hb.append(Part.makeCylinder(rp, depth + 1, V(x, y, seam - depth)))
            hl.append(Part.makeCylinder(rp, depth + 1, V(x, y, seam - 1)))
    return tb, tl, hb, hl, max(0.0, delta + rt - c["wall"])


def _slice_distance(a, b, z):
    wa = a.slice(V(0, 0, 1), z)
    wb = b.slice(V(0, 0, 1), z)
    if not wa or not wb:
        return None
    return Part.makeCompound(wa).distToShape(Part.makeCompound(wb))[0]


def _volume(shape):
    try:
        return max(0.0, shape.Volume)
    except Exception:
        return 0.0


def _in_cavity(bb, cavity, ri):
    """True si la envolvente cabe en la cavidad sin entrar en el redondeo de sus esquinas."""
    if not (all(bb[i] >= cavity[i] - 1e-6 for i in range(3)) and
            all(bb[i] <= cavity[i] + 1e-6 for i in range(3, 6))):
        return False
    if ri <= 0:
        return True
    hx, hy = cavity[3] - ri, cavity[4] - ri
    ax, ay = max(abs(bb[0]), abs(bb[3])), max(abs(bb[1]), abs(bb[4]))
    return ax <= hx + 1e-6 or ay <= hy + 1e-6 or math.hypot(ax - hx, ay - hy) <= ri + 1e-6


def _crossed_faces(bb, cavity):
    faces = []
    for name, out in (("izquierda", bb[0] < cavity[0] - 1e-3), ("derecha", bb[3] > cavity[3] + 1e-3),
                      ("frente", bb[1] < cavity[1] - 1e-3), ("fondo", bb[4] > cavity[4] + 1e-3),
                      ("tapa", bb[5] > cavity[5] + 1e-3), ("piso", bb[2] < cavity[2] - 1e-3)):
        if out:
            faces.append(name)
    return faces


def board_collision(board, base, lid, supports, d, c, bbs=None):
    """Intersección de la placa con la caja. Devuelve un dict con
    supports (mm³ contra soportes), shell (mm³ contra base/tapa), faces (caras
    que atraviesan los sólidos de la placa que chocan) y method.

    Un sólido de la placa cuya envolvente ajustada cabe en la cavidad no puede tocar
    paredes, piso, techo, lengüetas ni ranuras: con él solo se prueban los soportes.
    Los que salen de la cavidad (p. ej. conectores que sobresalen del PCB) se
    intersectan además con base y tapa (booleana real por sólido).
    """
    cavity = (-d["inner_x"]/2, -d["inner_y"]/2, c["floor"], d["inner_x"]/2, d["inner_y"]/2, d["ceiling"])
    ri = inner_corner_radius(c)
    out = {"supports": 0.0, "shell": 0.0, "faces": [], "method": "booleana por sólido"}
    parts = board.Solids
    if not parts:
        # Sin sólidos: estimación conservadora por envolventes.
        out["method"] = "envolventes (STEP sin sólidos)"
        bb = _box_bb(board)
        out["supports"] = float(sum(1 for t in supports if _bb_overlap(t, board, -1e-6)))
        if not _in_cavity(bb, cavity, ri):
            out["shell"], out["faces"] = 1.0, _crossed_faces(bb, cavity)
        return out
    faces = set()
    support_bbs = [_box_bb(t) for t in supports]
    for i, s in enumerate(parts):
        # Envolvente ajustada (de la caché si se pasó `bbs`): la BoundBox rápida puede ser
        # muy holgada y provocar booleanas innecesarias y lentas.
        sb = bbs[i] if bbs is not None and i < len(bbs) else _box_bb(s)
        for t, tb in zip(supports, support_bbs):
            if _bb_overlap(tb, sb, -1e-6):
                out["supports"] += _volume(s.common(t))
        if _in_cavity(sb, cavity, ri):
            continue
        if bbs is None:
            o, _ = _bound_box(s)
            sb = (o.XMin, o.YMin, o.ZMin, o.XMax, o.YMax, o.ZMax)
            if _in_cavity(sb, cavity, ri):
                continue
        v = sum(_volume(s.common(t)) for t in (base, lid) if _bb_overlap(t, sb, -1e-6))
        if v > 1e-6:
            out["shell"] += v
            faces.update(_crossed_faces(sb, cavity) or ["esquina"])
    out["faces"] = [f for f in LATERAL + ("tapa", "piso", "esquina") if f in faces]
    return out


def _lips(c, d, cutters, warnings):
    """Lengüetas («friccion»: dos tramos cortos por cara) o labio continuo («labio» e
    «imanes»: un tramo por cara, que las aberturas recortan). Devuelve
    (lengüetas, ranuras de la tapa, nervios, datos del ajuste)."""
    lips, slots, ribs, fit = [], [], [], {}
    if c["closure"] not in LIP_CLOSURES:
        return lips, slots, ribs, fit
    wall, radius, seam = c["wall"], c["radius"], d["seam"]
    ox, oy = d["outer_x"], d["outer_y"]
    th, gap = c["tongue_height"], c["clearance"]
    inter = c["rib_interference"] if c["closure"] != "imanes" else 0.0
    continuous = c["closure"] != "friccion"
    tk = min(1.6, wall - 2*MIN_SLOT_WALL - 2*gap)      # espesor de lengüeta
    if tk < 0.8 - EPS:
        raise CajaError("Pared insuficiente para lengüeta y ranura con este juego.")
    side_wall = (wall - tk - 2*gap)/2
    ch = min(0.6, 0.35*th)                              # alto del bisel de entrada
    bev = min(0.3, 0.3*tk)                              # retranqueo del bisel por lado
    straight = th - ch
    slot_top = th + 0.3
    fit.update(tongue_thickness=tk, slot_side_wall=side_wall, bevel_height=ch, bevel=bev)
    for side in LATERAL:
        length = ox if side in ("frente", "fondo") else oy
        free = length/2 - max(radius + 2, wall + 2)
        seglen = 2*free if continuous else min(16, free*0.65)
        if seglen < 4:
            raise CajaError("La caja es demasiado pequeña para las lengüetas. Usa tapa apoyada o amplía las medidas.")
        along_x = side in ("frente", "fondo")
        t = (oy if along_x else ox)/2 - wall/2
        t = -t if side in ("frente", "izquierda") else t

        def rect(cen, half_len, half_th):
            if along_x:
                return (cen - half_len, t - half_th, cen + half_len, t + half_th)
            return (t - half_th, cen - half_len, t + half_th, cen + half_len)

        centers = [0.0] if continuous else [s*free*0.53 for s in (-1, 1)]
        for center in centers:
            r0 = rect(center, seglen/2, tk/2)
            sx0, sy0, sx1, sy1 = rect(center, seglen/2 + gap, tk/2 + gap)
            slot_bb = (sx0, sy0, seam - 0.5, sx1, sy1, seam + slot_top)
            # Lengüetas cortas: si una abertura toca el tramo (o queda a menos de MIN_STRIP),
            # se omite entero. El labio continuo lo recortan las aberturas.
            if not continuous and any(_bb_overlap(slot_bb, k, MIN_STRIP) for k in cutters):
                warnings.append(f"Lengüeta omitida por una abertura en la cara {side}.")
                continue
            lips.append(ruled_solid([(r0, seam), (r0, seam + straight),
                                     (_shrink(r0, bev, bev), seam + th)]))
            slots.append(Part.makeBox(sx1 - sx0, sy1 - sy0, slot_top + 0.5, V(sx0, sy0, seam - 0.5)))
            if inter > 0:
                n = max(1, int(seglen // RIB_SPACING)) if continuous else 1
                for k in range(n):
                    rc = center - seglen/2 + seglen*(k + 0.5)/n
                    full = rect(rc, 0.6, tk/2 + gap + inter)
                    rib_bb = (min(full[0], full[2]), min(full[1], full[3]), seam,
                              max(full[0], full[2]), max(full[1], full[3]), seam + th)
                    if any(_bb_overlap(rib_bb, kk, MIN_STRIP) for kk in cutters):
                        continue
                    flush = rect(rc, 0.6, tk/2)
                    ribs.append(ruled_solid([(full, seam), (full, seam + straight*0.5), (flush, seam + straight)]))
    if len(lips) < 4:
        raise CajaError("Las aberturas dejan menos de cuatro lengüetas. Muévelas o usa tapa apoyada.")
    return lips, slots, ribs, fit


def build(config, cache=None):
    """Construye base y tapa. Con `cache` (BoardCache) la placa sale de la caché.

    result["board"] es la placa trasladada a su altura; result["board_local"] la
    placa normalizada sin trasladar (no mutar en sitio) y result["board_offset_z"]
    = floor + bottom_gap. result["board_key"] identifica la forma de placa usada.
    """
    if _IMPORT_ERROR is not None:
        raise CajaError("No se pudo importar FreeCAD: " + str(_IMPORT_ERROR))
    c = _prepare(config)
    board_local, size, info = cache.get(c) if cache is not None else read_board(c)
    board_local, size, info = fit_board(board_local, size, info, c)
    board = board_local.translated(V(0, 0, c["floor"] + c["bottom_gap"]))
    c["board_fit"] = info["fit"]
    c.update(zip(("board_x", "board_y", "board_z"), size))
    ix, iy = size[0] + 2*c["margin"], size[1] + 2*c["margin"]
    iz = c["bottom_gap"] + size[2] + c["top_gap"]
    ox, oy = ix + 2*c["wall"], iy + 2*c["wall"]
    total = c["floor"] + iz + c["lid"]
    ceiling = c["floor"] + iz                      # cara inferior del techo
    # Altura de la unión: la elegida, o techo − falda de la tapa.
    seam = c["split_height"] if c["split_height"] > 0 else ceiling - c["lid_depth"]
    c["lid_depth"] = ceiling - seam
    d = dict(inner_x=ix, inner_y=iy, inner_z=iz, outer_x=ox, outer_y=oy, outer_z=total, seam=seam,
             ceiling=ceiling, lid_skirt=ceiling - seam)
    dd = d
    warnings = list(info["warnings"]) + validate_build(c, dd)
    radius, wall = c["radius"], c["wall"]
    inner_radius = inner_corner_radius(c)
    walls = rounded_box(ox, oy, seam, radius).cut(rounded_box(ix, iy, iz + 1, inner_radius, c["floor"]))
    # Tapa en posición de cierre: falda de altura lid_depth; el hueco termina en el techo.
    lid = rounded_box(ox, oy, total - seam, radius, seam).cut(
        rounded_box(ix, iy, c["lid_depth"] + 1, inner_radius, seam - 1))
    cutters = [port_cutter(p, dd, wall) for p in c["ports"]]
    lips, slots, ribs, fit = _lips(c, dd, cutters, warnings)
    if c["closure"] == "apoyada":
        warnings.append("Tapa apoyada: no tiene retención; necesita una sujeción externa.")
    magnet_holes_base, magnet_holes_lid, towers_lid = [], [], []
    towers_base = []
    if c["closure"] == "imanes":
        towers_base, towers_lid, magnet_holes_base, magnet_holes_lid, out = magnet_towers(c, dd)
        for i, k in enumerate(cutters, 1):
            if any(_bb_overlap(k, t, MIN_STRIP) for t in towers_base + towers_lid):
                raise CajaError(f"Abertura {i}: choca con la torre de un imán en una esquina. "
                                "Aléjala de la esquina.")
        fit.update(magnet_towers=4, magnet_protrusion=out)
        warnings.append(f"Imanes Ø{_f(c['magnet_diameter'])} × {_f(c['magnet_height'])} mm: 8 en total "
                        f"(4 en la base y 4 en la tapa), en torres de esquina que sobresalen "
                        f"{_f(out)} mm. Colócalos de modo que cada par se atraiga.")
    support_solids = []
    for i, s in enumerate(c["supports"], 1):
        x, y, h, r, hole = s["x"], s["y"], s["height"], s["diameter"]/2, s["hole"]/2
        above = Part.makeCylinder(r, h, V(x, y, c["floor"]))
        if _volume(above.common(walls)) > 1e-4:
            raise CajaError(f"Soporte {i}: toca la pared de la base.")
        if any(_bb_overlap(above, k, 0.0) for k in cutters):
            raise CajaError(f"Soporte {i}: una abertura lo corta. Muévelo o mueve la abertura.")
        anchor = min(0.2, c["floor"]/2)
        st = Part.makeCylinder(r, h + anchor, V(x, y, c["floor"] - anchor))
        if hole > 0:
            # El agujero empieza en la cara superior del piso: nunca lo perfora.
            st = st.cut(Part.makeCylinder(hole, h + 1, V(x, y, c["floor"])))
        support_solids.append(st)
    # Apoyos bajo el PCB y pisadores desde la tapa (recortados donde hay componentes).
    frame = _pcb_frame(c, info, size)
    bbs = _solid_bbs(board, info, c)
    comps = _components(bbs, info)
    rests, notes = board_rests(c, dd, frame, comps)
    warnings += notes
    posts, notes = hold_down_posts(c, dd, frame, comps)
    warnings += notes
    tabs = [t for t in (port_lid_tab(p, dd, wall, c["clearance"]) for p in c["ports"]) if t is not None]
    plain_base = walls
    if lips or support_solids or rests or towers_base:
        plain_base = plain_base.multiFuse(lips + support_solids + rests + towers_base)
    lateral_cutters = [k for k, p in zip(cutters, c["ports"]) if p["side"] != "tapa"]
    if lateral_cutters or magnet_holes_base:
        plain_base = plain_base.cut(Part.makeCompound(lateral_cutters + magnet_holes_base))
    plain_base = plain_base.removeSplitter()
    base = plain_base.multiFuse(ribs).removeSplitter() if ribs else plain_base
    if towers_lid:
        lid = lid.multiFuse(towers_lid)
    lid_cuts = slots + cutters + magnet_holes_lid
    if lid_cuts:
        lid = lid.cut(Part.makeCompound(lid_cuts))
    if tabs or posts:
        # Después de las ranuras: la lengüeta que cierra una abertura no debe perder su borde.
        lid = lid.multiFuse(tabs + posts)
    lid = lid.removeSplitter()
    for label, shape in (("base", base), ("tapa", lid)):
        if len(shape.Solids) != 1:
            raise CajaError(f"La {label} quedó dividida en {len(shape.Solids)} piezas. Revisa aberturas y soportes.")
        if not shape.isValid():
            raise CajaError(f"La {label} quedó con geometría no válida. Revisa aberturas y soportes.")
    base, lid = base.Solids[0], lid.Solids[0]
    col = board_collision(board, base, lid, support_solids + rests + posts + tabs, dd, c, bbs)
    collision, collision_method = col["supports"] + col["shell"], col["method"]
    if col["supports"] > 0.001:
        raise CajaError(f"La placa intersecta los soportes, apoyos, pisadores o el cierre de una "
                        f"abertura ({col['supports']:.3f} mm³). Mueve o rebaja los soportes, o aumenta "
                        "la holgura inferior.")
    if col["shell"] > 0.001:
        caras = ", ".join(col["faces"]) or "pared"
        if info["fit"] in ("pcb", "personalizado"):
            # Sobresale de la medida elegida (p. ej. un conector lateral): se muestra igual
            # para que se vea dónde hace falta la abertura.
            warnings.append(f"Partes de la placa atraviesan la caja ({caras}; {collision_method}: "
                            f"{col['shell']:.1f} mm³). Agrega una abertura en esa cara (o usa «Detectar "
                            "aberturas») para que salgan, o usa la envolvente total.")
        else:
            raise CajaError(f"La placa intersecta la caja ({caras}; {col['shell']:.3f} mm³). "
                            "Aumenta las holguras.")
    hard = _volume(plain_base.common(lid))
    if hard > 0.001:
        raise CajaError(f"Colisión no prevista entre base y tapa cerradas ({hard:.3f} mm³).")
    fit_interference = _volume(Part.makeCompound(ribs).common(lid)) if ribs else 0.0
    if lips or tabs:
        # Inserción vertical: sin nervios no debe haber contacto a ninguna altura.
        lift = max([c["tongue_height"] if lips else 0.0] +
                   [_box_bb(t)[5] - _box_bb(t)[2] for t in tabs])
        for frac in (0.25, 0.5, 0.75, 1.0):
            displaced = lid.copy()
            displaced.translate(V(0, 0, frac*lift))
            if _volume(plain_base.common(displaced)) > 0.001:
                raise CajaError("La tapa colisiona con la base durante la inserción vertical.")
    if lips:
        # Juego lateral medido en una sección horizontal del tramo recto de la lengüeta.
        zc = seam + (c["tongue_height"] - fit["bevel_height"])/2
        fit["measured_clearance"] = _slice_distance(lips[0], lid, zc)
    fit.update(rests=len(rests), hold_down_posts=len(posts), port_tabs=len(tabs))
    return {"base": base, "lid": lid, "board": board, "config": c, "dimensions": d,
            "tongues": len(lips), "warnings": warnings, "collision": collision,
            "collision_method": collision_method, "fit_interference": fit_interference,
            "crossed_faces": col["faces"] if col["shell"] > 0.001 else [],
            "fit": fit, "board_info": info, "board_local": board_local,
            "board_key": info["board_key"], "board_offset_z": c["floor"] + c["bottom_gap"]}


# ---------------------------------------------------------------------------
# Componentes para aberturas en la tapa
# ---------------------------------------------------------------------------

# Nombres de modelo (KiCad) de componentes a los que se suele querer llegar desde arriba.
ACCESS_PATTERNS = ("conn", "pinheader", "pin_header", "pinsocket", "pin_socket", "socket", "header",
                   "jst", "molex", "terminalblock", "terminal_block", "screw_terminal", "usb", "barrel",
                   "jack", "sma", "bnc", "rj45", "idc", "potentiometer", "trimmer", "button", "sw_",
                   "switch", "dip")


def _display_name(label):
    """Nombre legible: sin el sufijo numérico que FreeCAD añade a etiquetas repetidas."""
    text = str(label)
    if len(text) > 3 and text[-3:].isdigit() and not text[-4].isdigit():
        text = text[:-3]
    return text


def list_components(board, info, c):
    """Componentes de la placa colocada en la caja (sin el PCB), para elegir aberturas en
    la tapa. Cada uno: id, name, bbox [x0, y0, z0, x1, y1, z1] (coordenadas de la caja),
    round (cabe en un cilindro vertical: abertura circular), suggested (su nombre parece un
    conector o un ajuste). Sin nombres del STEP (lectura sin GUI) cada sólido es un componente."""
    bbs = _solid_bbs(board, info, c)
    solids = board.Solids
    groups = info.get("solid_groups") or [(i, f"Sólido {i + 1}") for i in range(len(solids))]
    pcb = info.get("pcb_index")
    # Cavidad en planta: un componente que sale de ella atraviesa una pared lateral (p. ej. un
    # conector de borde) y ya se resuelve con una abertura lateral, no en la tapa.
    hx = _num(c, "board_x")/2 + _num(c, "margin")
    hy = _num(c, "board_y")/2 + _num(c, "margin")
    found = {}
    for i, (g, name) in enumerate(groups):
        if i == pcb or i >= len(bbs):
            continue
        item = found.setdefault(g, {"name": _display_name(name), "solids": [], "bb": list(bbs[i])})
        item["solids"].append(solids[i])
        b = item["bb"]
        item["bb"] = [min(b[0], bbs[i][0]), min(b[1], bbs[i][1]), min(b[2], bbs[i][2]),
                      max(b[3], bbs[i][3]), max(b[4], bbs[i][4]), max(b[5], bbs[i][5])]
    out = []
    for g, item in sorted(found.items()):
        b = item["bb"]
        w, h = b[3] - b[0], b[4] - b[1]
        round_ = False
        if w > 0 and h > 0 and abs(w - h) <= 0.15*max(w, h):
            cyl = Part.makeCylinder(max(w, h)/2 + 0.05, b[5] - b[2] + 2,
                                    V((b[0] + b[3])/2, (b[1] + b[4])/2, b[2] - 1))
            try:
                round_ = all(_volume(s.cut(cyl)) <= 1e-6 for s in item["solids"])
            except Exception:
                round_ = False
        low = item["name"].lower()
        edge = b[0] < -hx - 0.01 or b[3] > hx + 0.01 or b[1] < -hy - 0.01 or b[4] > hy + 0.01
        out.append({"id": int(g), "name": item["name"], "bbox": [round(x, 3) for x in b],
                    "round": round_, "edge": edge,
                    "suggested": not edge and any(k in low for k in ACCESS_PATTERNS)})
    return out


def _wall_slab(side, d, c):
    """Caja que ocupa la pared de una cara (o el techo de la tapa)."""
    ox, oy, ix, iy, total = d["outer_x"], d["outer_y"], d["inner_x"], d["inner_y"], d["outer_z"]
    if side == "frente":
        return Part.makeBox(ox, (oy - iy)/2, total, V(-ox/2, -oy/2, 0))
    if side == "fondo":
        return Part.makeBox(ox, (oy - iy)/2, total, V(-ox/2, iy/2, 0))
    if side == "izquierda":
        return Part.makeBox((ox - ix)/2, oy, total, V(-ox/2, -oy/2, 0))
    if side == "derecha":
        return Part.makeBox((ox - ix)/2, oy, total, V(ix/2, -oy/2, 0))
    ceiling = c["floor"] + d["inner_z"]
    return Part.makeBox(ox, oy, total - ceiling, V(-ox/2, -oy/2, ceiling))


def _merge_rects(rects, gap):
    """Une rectángulos [u0, v0, u1, v1, redondo] que se solapan o quedan a menos de `gap`."""
    rects = [list(r) for r in rects]
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                sep = max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])
                if sep < gap:
                    rects[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), False]
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects


def detect_ports(result, margin=0.5, to_seam=False):
    """Propone aberturas para los componentes de la placa que atraviesan la caja.

    Para cada sólido de la placa que toca la base o la tapa (las aberturas ya puestas
    no cuentan), mide la parte que queda dentro de la pared de cada cara y propone una
    abertura con `margin` de juego por lado: circular si esa sección es redonda,
    rectangular redondeada si no. Las que quedan a menos de MIN_STRIP se unen, y los
    bordes demasiado cerca del piso, la unión, el techo o el canto se llevan hasta él.
    Con `to_seam`, las laterales que quedan bajo la unión se abren hasta ella (y la
    tapa las cierra) para insertar la placa en vertical.
    Devuelve {"ports": [...], "skipped": [...], "components": n}.
    """
    c, d = result["config"], dict(result["dimensions"])
    d["ceiling"] = c["floor"] + d["inner_z"]
    board, base, lid = result["board"], result["base"], result["lid"]
    cavity = (-d["inner_x"]/2, -d["inner_y"]/2, c["floor"], d["inner_x"]/2, d["inner_y"]/2, d["ceiling"])
    ri = inner_corner_radius(c)
    seam, total, floor = d["seam"], d["outer_z"], c["floor"]
    found, skipped, count = {}, [], 0
    slabs = {}
    for s in board.Solids:
        o, _ = _bound_box(s)
        sb = (o.XMin, o.YMin, o.ZMin, o.XMax, o.YMax, o.ZMax)
        if _in_cavity(sb, cavity, ri):
            continue
        if sum(_volume(s.common(t)) for t in (base, lid) if _bb_overlap(t, sb, -1e-6)) <= 1e-6:
            continue
        count += 1
        for side in _crossed_faces(sb, cavity):
            if side == "piso":
                skipped.append("Un componente atraviesa el piso: no se crean aberturas en el piso.")
                continue
            slab = slabs.setdefault(side, _wall_slab(side, d, c))
            part = s.common(slab)
            vol = _volume(part)
            if vol <= 1e-6:
                continue
            pb, _ = _bound_box(part)
            if side in ("frente", "fondo"):
                r = [pb.XMin, pb.ZMin, pb.XMax, pb.ZMax]
                axis = V(0, 1, 0)
                start = V((r[0] + r[2])/2, pb.YMin - 1, (r[1] + r[3])/2)
                length = pb.YLength + 2
            elif side in ("izquierda", "derecha"):
                r = [pb.YMin, pb.ZMin, pb.YMax, pb.ZMax]
                axis = V(1, 0, 0)
                start = V(pb.XMin - 1, (r[0] + r[2])/2, (r[1] + r[3])/2)
                length = pb.XLength + 2
            else:
                r = [pb.XMin, pb.YMin, pb.XMax, pb.YMax]
                axis = V(0, 0, 1)
                start = V((r[0] + r[2])/2, (r[1] + r[3])/2, pb.ZMin - 1)
                length = pb.ZLength + 2
            w, h = r[2] - r[0], r[3] - r[1]
            # Redonda solo si la parte cabe entera en el cilindro de la abertura (una rosca
            # sí; una tuerca o un cuerpo cuadrado dentro de la pared no).
            round_ = False
            if abs(w - h) <= 0.15*max(w, h):
                cyl = Part.makeCylinder(max(w, h)/2 + margin, length, start, axis)
                round_ = _volume(part.cut(cyl)) <= 1e-6
            found.setdefault(side, []).append(r + [round_])
    ports = []
    for side in LATERAL + ("tapa",):
        rects = [[a - margin, b - margin, cc + margin, dd_ + margin, rnd]
                 for a, b, cc, dd_, rnd in found.get(side, [])]
        for u0, v0, u1, v1, rnd in _merge_rects(rects, MIN_STRIP):
            orig = (u0, v0, u1, v1)
            if side == "tapa":
                hx, hy = d["inner_x"]/2, d["inner_y"]/2
                u0, u1, v0, v1 = max(u0, -hx), min(u1, hx), max(v0, -hy), min(v1, hy)
            else:
                half = (d["outer_y"] if side in ("izquierda", "derecha") else d["outer_x"])/2
                u0, u1 = max(u0, -half), min(u1, half)
                if half + u0 < MIN_STRIP:
                    u0 = -half
                if half - u1 < MIN_STRIP:
                    u1 = half
                v0, v1 = max(v0, floor), min(v1, total)
                if v0 - floor < MIN_STRIP:
                    v0 = floor
                if total - v1 < MIN_STRIP:
                    v1 = total
                if not (to_seam and v1 < seam):
                    if 0 < seam - v1 < MIN_STRIP:
                        v1 = seam
                    if 0 < v0 - seam < MIN_STRIP:
                        v0 = seam
            snapped = any(abs(a - b) > 1e-6 for a, b in zip(orig, (u0, v0, u1, v1)))
            w, h = u1 - u0, v1 - v0
            if w <= 0.1 or h <= 0.1:
                continue
            # Con radio ≤ juego, las esquinas redondeadas siguen cubriendo las del componente.
            rr = round(min(margin, 1.0, min(w, h)/4), 2)
            p = {"side": side, "u": round((u0 + u1)/2, 2), "v": round((v0 + v1)/2, 2),
                 "width": math.ceil(w*100)/100, "height": math.ceil(h*100)/100,
                 "shape": "redondeada" if rr > 0.05 else "rectangular",
                 "radius": rr, "to_seam": False}
            if rnd and not snapped:
                dia = math.ceil(max(w, h)*100)/100
                if side == "tapa" or p["v"] - dia/2 >= floor - EPS:
                    p.update(shape="circular", width=dia, height=dia, radius=0.0)
            if to_seam and side != "tapa" and v1 < seam and v0 < seam:
                p["to_seam"] = True
            ports.append(p)
    return {"ports": ports, "skipped": sorted(set(skipped)), "components": count}


def display_shapes(result, view):
    base, lid, board = (result[k].copy() for k in ("base", "lid", "board"))
    d = result["dimensions"]
    if view == "separada":
        lid.rotate(V(), V(0, 1, 0), 180)
        lid.translate(V(d["outer_x"] + 12, 0, d["outer_z"]))
    elif view == "explosionada":
        lid.translate(V(0, 0, max(12, d["inner_z"]*0.6)))
    return base, lid, board


def printable_shapes(result):
    """Base tal cual y tapa invertida (techo sobre la cama), ambas con z mínimo 0."""
    base, lid = result["base"].copy(), result["lid"].copy()
    lid.rotate(V(), V(0, 1, 0), 180)
    lid.translate(V(0, 0, result["dimensions"]["outer_z"]))
    return base, lid


COLOR_BASE = (0.29, 0.68, 0.82)
COLOR_TAPA = (0.95, 0.67, 0.30)
COLOR_PLACA = (0.22, 0.64, 0.35)
VIEWS = ("separada", "cerrada", "explosionada")


def _view_object(obj):
    """ViewObject si hay GUI y el documento lo tiene (los documentos ocultos pueden no tenerlo)."""
    if not App.GuiUp:
        return None
    return getattr(obj, "ViewObject", None)


def _set_params(params, result):
    """Rellena (o actualiza) las propiedades del objeto de parámetros."""
    c, d = result["config"], result["dimensions"]
    props = params.PropertiesList
    if "ConfiguracionJSON" not in props:
        params.addProperty("App::PropertyString", "ConfiguracionJSON", "Proyecto")
    params.ConfiguracionJSON = json.dumps({k: v for k, v in c.items()}, ensure_ascii=False, default=str)
    for name, key in (("AnchoExterior", "outer_x"), ("LargoExterior", "outer_y"), ("AltoExterior", "outer_z"),
                      ("AlturaUnion", "seam")):
        if name not in props:
            params.addProperty("App::PropertyLength", name, "Medidas calculadas")
        setattr(params, name, d[key])
        params.setEditorMode(name, 1)
    if "Advertencias" not in props:
        params.addProperty("App::PropertyString", "Advertencias", "Proyecto")
    notes = list(result["warnings"])
    if result.get("tongues"):
        notes.append("Ajuste por fricción: requiere una impresión de prueba")
    params.Advertencias = "; ".join(notes)
    if "Edicion" not in props:
        params.addProperty("App::PropertyString", "Edicion", "Proyecto")
    params.Edicion = ("Editar el proyecto JSON en la aplicación o CONFIG en la macro y regenerar. "
                      "Las piezas son sólidos BREP sin historial.")


def _new_document(name, hidden=False):
    if hidden:
        try:
            return App.newDocument(name, hidden=True)
        except TypeError:
            pass
    return App.newDocument(name)


def create_document(config, result=None, hidden=False, fit_view=True):
    """Documento NUEVO con base, tapa (vista config["view"]), placa y parámetros.

    hidden=True crea un documento oculto (para exportar sin tocar la vista del
    usuario) y fit_view=False no cambia la cámara de la vista activa.
    """
    result = result or build(config)
    c = result["config"]
    doc = _new_document("CajaCircuito", hidden)
    doc.Label = str(c.get("name") or "Caja circuito")
    group = doc.addObject("App::DocumentObjectGroup", "Caja")
    group.Label = "Caja - piezas para imprimir"
    view = c.get("view", "separada")
    shapes = display_shapes(result, view)
    for name, label, shape, color in (("Base", "Base con encaje", shapes[0], COLOR_BASE),
                                      ("Tapa", "Tapa", shapes[1], COLOR_TAPA)):
        obj = doc.addObject("Part::Feature", name)
        obj.Label = label
        obj.Shape = shape
        group.addObject(obj)
        vo = _view_object(obj)
        if vo is not None:
            vo.ShapeColor = color
    obj = doc.addObject("Part::Feature", "Placa")
    obj.Label = "Placa STEP (referencia)" if c.get("step_path") else "Envolvente manual (referencia)"
    obj.Shape = shapes[2]
    obj.Visibility = view != "cerrada"
    vo = _view_object(obj)
    if vo is not None:
        colors = result.get("board_info", {}).get("face_colors")
        if colors and len(colors) == len(obj.Shape.Faces):
            vo.DiffuseColor = [tuple(c) for c in colors]     # colores del STEP (KiCad)
        else:
            vo.ShapeColor = COLOR_PLACA
        vo.Transparency = 25
    params = doc.addObject("App::FeaturePython", "Parametros")
    params.Label = "Configuración - regenerar con macro o aplicación"
    _set_params(params, result)
    doc.recompute()
    if App.GuiUp and fit_view and not hidden:
        try:
            import FreeCADGui as Gui
            Gui.activeDocument().activeView().viewAxonometric()
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass
    return doc, result


# ---------------------------------------------------------------------------
# Documento persistente (visor FreeCAD administrado por la aplicación)
# ---------------------------------------------------------------------------

SYNC_NAMES = {"base": "CajaBase", "lid": "CajaTapa", "board": "CajaPlaca", "params": "CajaParametros"}


def _neutral(shape):
    """Forma con ubicación identidad (envuelve en un compuesto si hace falta).

    Asignar Placement a un Part::Feature reemplaza la ubicación propia de la
    forma; con ubicación identidad el Placement se compone sin perder nada.
    """
    if shape.Placement.isIdentity():
        return shape
    return Part.makeCompound([shape])


def lid_placement(result, view):
    """Placement de la tapa (forma en posición de cierre) equivalente a display_shapes."""
    if view not in VIEWS:
        raise CajaError("Vista no reconocida: " + str(view))
    d = result["dimensions"]
    if view == "separada":
        return App.Placement(V(d["outer_x"] + 12, 0, d["outer_z"]), App.Rotation(V(0, 1, 0), 180))
    if view == "explosionada":
        return App.Placement(V(0, 0, max(12, d["inner_z"]*0.6)), App.Rotation())
    return App.Placement()


def _feature(doc, name, label, created):
    obj = doc.getObject(name)
    if obj is None:
        obj = doc.addObject("Part::Feature", name)
        created.append(name)
    if obj.Label != label:
        obj.Label = label
    return obj


def _board_label(step):
    return "Placa STEP (referencia)" if step else "Envolvente manual (referencia)"


def _apply_board(doc, shape, board_key, state, offset_z, created, step=True, colors=None):
    """Crea/actualiza CajaPlaca. Reasigna la forma solo si cambia board_key. `colors`: un
    color por cara (los del STEP); sin ellos, la placa va en verde."""
    obj = _feature(doc, SYNC_NAMES["board"], _board_label(step), created)
    if "CajaClavePlaca" not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", "CajaClavePlaca", "Caja")
        obj.setEditorMode("CajaClavePlaca", 2)
    last = state.get("board_key") if state is not None else obj.CajaClavePlaca
    fresh = SYNC_NAMES["board"] in created
    reassigned = fresh or last != board_key or obj.CajaClavePlaca != board_key or obj.Shape.isNull()
    if reassigned:
        obj.Shape = _neutral(shape)
        obj.CajaClavePlaca = str(board_key)
    obj.Placement = App.Placement(V(0, 0, offset_z), App.Rotation())   # siempre después de Shape
    if state is not None:
        state["board_key"] = board_key
    vo = _view_object(obj)
    if vo is not None:
        if fresh:
            vo.Transparency = 25
        if fresh or reassigned:
            if colors and len(colors) == len(obj.Shape.Faces):
                vo.DiffuseColor = [tuple(c) for c in colors]
            else:
                vo.ShapeColor = COLOR_PLACA
    return obj, reassigned


def _apply_display(doc, visible, transparency):
    names = {"base": SYNC_NAMES["base"], "lid": SYNC_NAMES["lid"], "board": SYNC_NAMES["board"]}
    for key, flag in (visible or {}).items():
        obj = doc.getObject(names.get(key, ""))
        if obj is not None:
            obj.Visibility = bool(flag)
    for key, value in (transparency or {}).items():
        obj = doc.getObject(names.get(key, ""))
        vo = _view_object(obj) if obj is not None else None
        if vo is not None:
            vo.Transparency = int(max(0, min(100, round(float(value)))))


def show_board(doc, shape, board_key, state, offset_z=0.0, colors=None):
    """Muestra solo la placa (p. ej. recién cargada) en CajaPlaca y oculta base y tapa.

    Reasigna la forma solo si board_key difiere de state["board_key"].
    Devuelve {"board_reassigned": bool, "created": [...]}.
    """
    created = []
    obj, reassigned = _apply_board(doc, shape, board_key, state, offset_z, created,
                                   step=not str(board_key).startswith("manual:"), colors=colors)
    obj.Visibility = True
    for key in ("base", "lid"):
        other = doc.getObject(SYNC_NAMES[key])
        if other is not None:
            other.Visibility = False
    if state is not None:
        state["board_only"] = True
    doc.recompute()
    return {"board_reassigned": reassigned, "created": created}


def sync_document(doc, result, view="cerrada", visible=None, transparency=None, state=None):
    """Crea o actualiza en `doc` (existente) CajaBase, CajaTapa, CajaPlaca y CajaParametros.

    Base y tapa se reasignan siempre (en posición de cierre); la vista de la tapa
    va por Placement (igual que display_shapes). La placa solo se reasigna si
    result["board_key"] difiere de state["board_key"] (o de la clave guardada
    en el objeto si state es None); su altura va por Placement (board_offset_z).
    visible = {base, lid, board: bool}; transparency = {base, lid, board: 0..100}
    (la transparencia solo con GUI). Devuelve un dict con lo reasignado.
    """
    if view not in VIEWS:
        raise CajaError("Vista no reconocida: " + str(view))
    created = []
    out = {}
    for key, label, color in (("base", "Base con encaje", COLOR_BASE), ("lid", "Tapa", COLOR_TAPA)):
        obj = _feature(doc, SYNC_NAMES[key], label, created)
        obj.Shape = _neutral(result[key])
        obj.Placement = lid_placement(result, view) if key == "lid" else App.Placement()
        out[key + "_reassigned"] = True
        if SYNC_NAMES[key] in created:
            vo = _view_object(obj)
            if vo is not None:
                vo.ShapeColor = color
    step = bool(result["config"].get("step_path"))
    _, out["board_reassigned"] = _apply_board(doc, result["board_local"], result["board_key"], state,
                                              result["board_offset_z"], created, step,
                                              result["board_info"].get("face_colors"))
    params = doc.getObject(SYNC_NAMES["params"])
    if params is None:
        params = doc.addObject("App::FeaturePython", SYNC_NAMES["params"])
        params.Label = "Configuración - regenerar con macro o aplicación"
        created.append(SYNC_NAMES["params"])
    _set_params(params, result)
    board_only = bool(state.pop("board_only", False)) if state is not None else False
    if visible is None and (board_only or any(SYNC_NAMES[k] in created for k in ("base", "lid"))):
        visible = {"base": True, "lid": True}    # base/tapa nuevas o tras show_board
    elif visible is None:
        visible = {}
    _apply_display(doc, visible, transparency)
    doc.recompute()
    out.update(created=created, board_key=result["board_key"], view=view)
    return out


def apply_view(doc, result, view, visible=None, transparency=None):
    """Cambia solo Placement, visibilidad y transparencia (no toca formas)."""
    lid = doc.getObject(SYNC_NAMES["lid"])
    board = doc.getObject(SYNC_NAMES["board"])
    if lid is None or board is None:
        raise CajaError("El documento no tiene la caja sincronizada; llama antes a sync_document.")
    lid.Placement = lid_placement(result, view)
    board.Placement = App.Placement(V(0, 0, result["board_offset_z"]), App.Rotation())
    base = doc.getObject(SYNC_NAMES["base"])
    if base is not None:
        base.Placement = App.Placement()
    _apply_display(doc, visible, transparency)
    return {"view": view, "board_reassigned": False}


def mesh_data(shape, limit=6000):
    """Triángulos [[x,y,z]x3] para la vista previa (≤ limit). Nunca afecta al CAD exportado."""
    import MeshPart
    b = shape.BoundBox
    diag = max(1.0, math.sqrt(b.XLength**2 + b.YLength**2 + b.ZLength**2)) if b.isValid() else 100.0
    lin = max(0.08, diag/500)
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=lin, AngularDeflection=0.5, Relative=False)
    if mesh.CountFacets > limit:
        try:
            mesh.decimate(lin*2, max(0.0, min(0.99, 1 - 0.9*limit/mesh.CountFacets)))
        except Exception:
            pass
    points, facets = mesh.Topology
    stride = max(1, math.ceil(len(facets)/limit))
    pts = [(round(p.x, 3), round(p.y, 3), round(p.z, 3)) for p in points]
    return [[list(pts[i]) for i in f] for f in facets[::stride]]


def _write_file(dest, writer):
    """Escribe con OCC; si la ruta no ASCII falla, usa un temporal ASCII y mueve el archivo."""
    try:
        writer(str(dest))
        if Path(dest).is_file() and Path(dest).stat().st_size > 0:
            return
    except Exception:
        pass
    tmpdir = tempfile.mkdtemp(prefix="cajas_out_")
    try:
        tmp = os.path.join(tmpdir, "salida" + Path(dest).suffix)
        writer(tmp)
        shutil.move(tmp, str(dest))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _summary(result):
    c = result["config"]
    return {"dimensions": result["dimensions"], "size": [c[k] for k in ("board_x", "board_y", "board_z")],
            "tongues": result["tongues"], "warnings": result["warnings"],
            "fit_interference": result["fit_interference"], "collision": result["collision"],
            "collision_method": result["collision_method"], "fit": result["fit"],
            **board_summary(result["board_info"]), "crossed_faces": result.get("crossed_faces", [])}


def board_summary(info):
    """Datos de la placa para la interfaz: medida usada, medidas total y del PCB."""
    return {"board_fit": info.get("fit"), "sizes": info.get("sizes"), "pcb_z": info.get("pcb_z")}


def _export_folder(folder, c):
    """Valida la carpeta y que no se sobrescriba nada. Devuelve (Path, nombres)."""
    if not str(folder or "").strip():
        raise CajaError("Falta la carpeta de exportación.")
    folder = Path(folder)
    names = ["Caja.FCStd"]
    for n in ("Base", "Tapa"):
        if c.get("export_stl"):
            names.append(n + ".stl")
        if c.get("export_step"):
            names.append(n + ".step")
    existing = [n for n in names if (folder/n).exists()]
    if existing:
        raise CajaError("La carpeta ya contiene " + ", ".join(existing) +
                        ". Elige una carpeta nueva para conservar las versiones.")
    return folder, names


def export_result(result, folder, config=None):
    """Exporta un result ya calculado: Caja.FCStd (documento nuevo oculto que se cierra)
    y Base/Tapa en STL/STEP orientadas para imprimir. No vuelve a leer el STEP.

    `config` (opcional) decide export_stl/export_step; por defecto result["config"].
    Nunca sobrescribe: si existe algún archivo de salida, lanza CajaError. Devuelve
    la lista de rutas escritas.
    """
    c = config if config is not None else result["config"]
    folder, _ = _export_folder(folder, c)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/"Caja.FCStd"
    doc, result = create_document(c, result, hidden=True, fit_view=False)
    try:
        _write_file(target, doc.saveAs)
    finally:
        App.closeDocument(doc.Name)
    files = [str(target)]
    for name, shape in zip(("Base", "Tapa"), printable_shapes(result)):
        if c.get("export_stl"):
            import MeshPart
            dest = folder/(name + ".stl")
            mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.05, AngularDeflection=0.2, Relative=False)
            _write_file(dest, mesh.write)
            files.append(str(dest))
        if c.get("export_step"):
            dest = folder/(name + ".step")
            _write_file(dest, shape.exportStep)
            files.append(str(dest))
    return files


def run_worker(request):
    if _IMPORT_ERROR is not None:
        raise CajaError("No se pudo importar FreeCAD/Part. Ejecuta el worker con el Python de FreeCAD: "
                        + str(_IMPORT_ERROR))
    action = request.get("action")
    c = request.get("config") or {}
    if action == "inspect":
        shape, size, info = fit_board(*read_board(c), c)
        return {"size": size, "mesh": mesh_data(shape), "solids": len(shape.Solids),
                "faces": len(shape.Faces), "warnings": info["warnings"], "bbox_method": info["bbox_method"],
                **board_summary(info)}
    if action not in ("preview", "export"):
        raise CajaError("Acción desconocida: " + str(action))
    view = c.get("view", "separada")
    if action == "export":
        _export_folder(request.get("folder"), c)    # antes de construir: falla rápido
    result = build(c)
    response = _summary(result)
    if action == "preview":
        shapes = display_shapes(result, view)
        response["meshes"] = {k: mesh_data(s) for k, s in zip(("base", "lid", "board"), shapes)}
        return response
    response["files"] = export_result(result, request.get("folder"), c)
    return response


# WORKER_ENTRY
def _main(argv):
    import sys
    import traceback
    if len(argv) < 3:
        sys.stderr.write("Uso: python cajas_freecad.py <request.json> <response.json>\n")
        return 2
    response_path = Path(argv[2])
    try:
        request = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
        response = {"ok": True, "result": run_worker(request)}
    except CajaError as exc:
        response = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
    except Exception as exc:
        response = {"ok": False, "error": "Error interno al generar la geometría con FreeCAD ("
                    + type(exc).__name__ + "): " + str(exc), "traceback": traceback.format_exc()}
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))

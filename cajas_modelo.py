"""Configuración, proyectos y exportación de macros de «Cajas para circuitos».

Importable sin FreeCAD ni interfaz gráfica (Python 3.10+; también con el Python
3.11 incluido en FreeCAD 1.0). Todas las medidas están en mm.

Convenciones de coordenadas:
- Origen XY en el centro de la caja; z = 0 en la base exterior.
- Puertos laterales: u = posición horizontal a lo largo de la cara (X en
  frente/fondo, Y en izquierda/derecha), v = altura del centro desde z = 0.
- Puertos de tapa: u = X, v = Y.
- Soportes: x, y del centro; height medida desde la cara superior del piso.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import os
import pprint
import re
import tempfile
from pathlib import Path

PROJECT_FORMAT = "cajas-circuitos"
PROJECT_VERSION = 1
WORKER_MARKER = "# WORKER_ENTRY"
CORE_FILE = Path(__file__).resolve().with_name("cajas_freecad.py")

SIDES = ("frente", "fondo", "izquierda", "derecha", "tapa")
LATERAL_SIDES = SIDES[:4]
CLOSURES = ("friccion", "labio", "imanes", "tornillos", "insertos", "apoyada")   # ver cajas_freecad
LIP_CLOSURES = ("friccion", "labio", "imanes", "tornillos", "insertos")
SCREW_CLOSURES = ("tornillos", "insertos")
SCREW_HEADS = ("cilindrica", "avellanada")
# Igual que cajas_freecad.SCREWS: (paso libre en la tapa, agujero en la base sin inserto, cabeza
# cilíndrica Ø y alto, cabeza avellanada Ø, agujero del inserto Ø y largo del inserto).
SCREWS = {
    "M2": (2.4, 2.0, 4.3, 2.3, 4.4, 3.2, 4.0),
    "M2.5": (2.9, 2.5, 5.0, 2.8, 5.4, 3.6, 5.0),
    "M3": (3.4, 3.0, 6.0, 3.3, 6.4, 4.0, 5.7),
    "M4": (4.5, 4.0, 7.5, 4.3, 8.4, 5.4, 8.1),
}
_SCREW_FLOOR = 0.8
VIEWS = ("separada", "cerrada", "explosionada")
AXES = ("X", "Y", "Z")
# Medida de placa que dimensiona la caja con STEP (ver cajas_freecad.fit_board).
BOARD_FITS = ("pcb", "total", "personalizado")
BOARD_RESTS = ("ninguno", "esquinas", "repisa")
PORT_SHAPES = ("rectangular", "redondeada", "circular", "ovalada")
TIGHT_MARGIN = 0.2
_CORNER_FACTOR = 1 - 1/math.sqrt(2)
_MAGNET_POCKET_EXTRA = 0.2 + 0.6     # alojamiento más hondo que el imán + material bajo él

DEFAULTS = {
    "name": "Caja_circuito", "step_path": "", "up_axis": "Z", "rotation": 0.0, "board_fit": "pcb",
    "board_x": 67.0, "board_y": 67.0, "board_z": 12.0, "pcb_offset": 0.0,
    "margin": 3.0, "bottom_gap": 3.0, "top_gap": 3.0,
    "wall": 3.0, "floor": 2.4, "lid": 2.4, "radius": 4.0,
    "board_rest": "esquinas", "rest_width": 2.0, "hold_down": False,
    "lid_depth": 3.0, "split_height": 0.0, "closure": "friccion", "tongue_height": 1.8,
    "clearance": 0.15, "rib_interference": 0.02,
    "magnet_diameter": 6.0, "magnet_height": 2.0, "magnet_clearance": 0.15,
    "screw_size": "M3", "screw_length": 10.0, "screw_head": "cilindrica",
    "screw_hole": 0.0, "insert_depth": 0.0,
    "supports": [], "ports": [], "view": "separada", "export_stl": True,
    "export_step": False,
}

SUPPORT_DEFAULTS = {"x": None, "y": None, "height": None, "diameter": None, "hole": 0.0}
PORT_DEFAULTS = {"side": None, "u": 0.0, "v": None, "width": None, "height": None,
                 "shape": "rectangular", "radius": 1.0, "to_seam": False}

LABELS = {
    "name": "Nombre",
    "step_path": "Archivo STEP de la placa",
    "up_axis": "Eje vertical del STEP",
    "rotation": "Rotación en Z",
    "board_fit": "Medida de la placa",
    "board_x": "Ancho de placa (X)",
    "board_y": "Largo de placa (Y)",
    "board_z": "Alto de placa (Z)",
    "pcb_offset": "Desfase del PCB sobre la base del STEP",
    "margin": "Holgura lateral",
    "bottom_gap": "Holgura inferior",
    "top_gap": "Holgura superior",
    "wall": "Pared",
    "floor": "Piso",
    "lid": "Techo de tapa",
    "radius": "Radio de esquina",
    "board_rest": "Apoyo de la placa",
    "rest_width": "Ancho del apoyo",
    "hold_down": "Pisadores en la tapa",
    "lid_depth": "Profundidad de tapa",
    "split_height": "Altura de la unión",
    "closure": "Cierre",
    "magnet_diameter": "Diámetro del imán",
    "magnet_height": "Alto del imán",
    "magnet_clearance": "Holgura del imán",
    "screw_size": "Métrica del tornillo",
    "screw_length": "Largo del tornillo",
    "screw_head": "Cabeza del tornillo",
    "screw_hole": "Agujero en la base",
    "insert_depth": "Largo del inserto",
    "tongue_height": "Altura de lengüeta",
    "clearance": "Juego del encaje",
    "rib_interference": "Interferencia de nervios",
    "supports": "Soportes",
    "ports": "Aberturas",
    "view": "Vista",
    "export_stl": "Exportar STL",
    "export_step": "Exportar STEP",
}

SUPPORT_LABELS = {"x": "X", "y": "Y", "height": "Altura", "diameter": "Diámetro",
                  "hole": "Agujero"}
PORT_LABELS = {"side": "Cara", "u": "Posición u", "v": "Posición v", "width": "Ancho",
               "height": "Alto", "shape": "Forma", "radius": "Radio de esquinas",
               "to_seam": "Abrir hasta la unión"}

_NUMERIC = [k for k, v in DEFAULTS.items() if isinstance(v, float)]
_BOOLEAN = ("export_stl", "export_step", "hold_down")
_TRUE = {"1", "true", "t", "si", "sí", "s", "yes", "y", "on", "verdadero"}
_FALSE = {"0", "false", "f", "no", "n", "off", "falso", ""}


class ConfigError(ValueError):
    """Error de configuración con mensaje en español. `field` es la clave afectada."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def _fmt(x):
    """Número con coma decimal para los mensajes."""
    return f"{x:g}".replace(".", ",")


def number(value, label="Valor"):
    """Convierte a float finito; acepta coma decimal. Lanza ConfigError."""
    if isinstance(value, bool):
        raise ConfigError(f"{label}: se esperaba un número.")
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            text = str(value).strip().replace(" ", "").replace(",", ".")
            result = float(text)
    except (TypeError, ValueError):
        raise ConfigError(f"{label}: «{value}» no es un número válido.") from None
    if not math.isfinite(result):
        raise ConfigError(f"{label}: el número debe ser finito.")
    return result


def boolean(value, label="Valor"):
    """Convierte True/False, 1/0, "true"/"false", "sí"/"no" a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise ConfigError(f"{label}: «{value}» no es un valor sí/no válido.")


def _choice(value, options, label, key, transform=str.lower):
    """Valor de una lista cerrada de opciones; lanza ConfigError si no es válido."""
    text = transform(str(value).strip())
    text = text.replace("fricción", "friccion")
    if text not in options:
        raise ConfigError(f"{label}: «{value}» no es válido (usa {', '.join(options)}).", key)
    return text


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name, default="Caja"):
    """Nombre de archivo/carpeta válido en Windows. Conserva acentos; nunca vacío."""
    text = _FORBIDDEN.sub("_", str(name or "").strip()).strip().rstrip(". ")
    text = text[:80].rstrip(". ")
    if not text or set(text) <= {"_"}:
        text = default
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    return text


# ---------------------------------------------------------------------------
# Normalización y validación
# ---------------------------------------------------------------------------

def _normalize_item(raw, defaults, labels, kind, index):
    """Normaliza un soporte o una abertura con sus valores por defecto."""
    prefix = f"{kind} {index}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{prefix}: formato inválido (se esperaba un diccionario).")
    item = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        if value is None or (isinstance(value, str) and not value.strip() and default is None):
            raise ConfigError(f"{prefix}: falta el campo «{labels[key]}».")
        if key == "side":
            item[key] = _choice(value, SIDES, f"{prefix}: {labels[key]}", key)
        elif key == "shape":
            item[key] = _choice(value, PORT_SHAPES, f"{prefix}: {labels[key]}", key)
        elif key == "to_seam":
            item[key] = boolean(value, f"{prefix}: {labels[key]}")
        else:
            item[key] = number(value, f"{prefix}: {labels[key]}")
    if item.get("shape") == "circular":
        item["height"] = item["width"]          # circular: alto = diámetro = ancho
    return item


def normalize(data):
    """Copia profunda con tipos normalizados y solo claves conocidas. No valida rangos."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("La configuración debe ser un diccionario.")
    data = copy.deepcopy(data)
    c = copy.deepcopy(DEFAULTS)
    for key in DEFAULTS:
        if key in data and data[key] is not None:
            c[key] = data[key]
    for key in _NUMERIC:
        try:
            c[key] = number(c[key], LABELS[key])
        except ConfigError as exc:
            exc.field = key
            raise
    for key in _BOOLEAN:
        try:
            c[key] = boolean(c[key], LABELS[key])
        except ConfigError as exc:
            exc.field = key
            raise
    c["name"] = str(c["name"]).strip()
    c["step_path"] = str(c["step_path"]).strip()
    c["up_axis"] = _choice(c["up_axis"], AXES, LABELS["up_axis"], "up_axis", str.upper)
    c["board_fit"] = _choice(c["board_fit"], BOARD_FITS, LABELS["board_fit"], "board_fit")
    c["closure"] = _choice(c["closure"], CLOSURES, LABELS["closure"], "closure")
    c["board_rest"] = _choice(c["board_rest"], BOARD_RESTS, LABELS["board_rest"], "board_rest")
    c["screw_size"] = _choice(c["screw_size"], tuple(SCREWS), LABELS["screw_size"], "screw_size", str.upper)
    c["screw_head"] = _choice(str(c["screw_head"]).replace("í", "i"), SCREW_HEADS, LABELS["screw_head"],
                              "screw_head")
    c["view"] = _choice(c["view"], VIEWS, LABELS["view"], "view")
    c["rotation"] = c["rotation"] % 360.0
    if c["rotation"] == 360.0:  # -1e-20 % 360 da 360.0
        c["rotation"] = 0.0
    for key, kind in (("supports", "Soporte"), ("ports", "Abertura")):
        if not isinstance(c[key], (list, tuple)):
            raise ConfigError(f"{LABELS[key]}: se esperaba una lista.", key)
    c["supports"] = [_normalize_item(s, SUPPORT_DEFAULTS, SUPPORT_LABELS, "Soporte", i)
                     for i, s in enumerate(c["supports"], 1)]
    ports = []
    for i, p in enumerate(c["ports"], 1):
        defaults = PORT_DEFAULTS
        # En la tapa v es la coordenada Y: centrada por defecto.
        if isinstance(p, dict) and str(p.get("side", "")).strip().lower() == "tapa":
            defaults = {**PORT_DEFAULTS, "v": 0.0}
        ports.append(_normalize_item(p, defaults, PORT_LABELS, "Abertura", i))
    c["ports"] = ports
    return c


def dimensions(c):
    """Medidas derivadas (mismas fórmulas que cajas_freecad.build)."""
    ix = c["board_x"] + 2*c["margin"]
    iy = c["board_y"] + 2*c["margin"]
    iz = c["bottom_gap"] + c["board_z"] + c["top_gap"]
    ceiling = c["floor"] + iz
    # Unión: la altura elegida, o techo − falda de la tapa.
    seam = c["split_height"] if c.get("split_height", 0) > 0 else ceiling - c["lid_depth"]
    return {"inner_x": ix, "inner_y": iy, "inner_z": iz,
            "outer_x": ix + 2*c["wall"], "outer_y": iy + 2*c["wall"],
            "outer_z": ceiling + c["lid"], "ceiling": ceiling,
            "seam": seam, "lid_skirt": ceiling - seam}


def port_rect(p, seam):
    """(u0, v0, u1, v1) que ocupa la abertura (incluye la ranura hasta la unión)."""
    u, v, w, h = p["u"], p["v"], p["width"], p["height"]
    top = v + h/2
    if p.get("to_seam") and p["side"] != "tapa" and v - h/2 < seam:
        top = max(top, seam)
    return (u - w/2, v - h/2, u + w/2, top)


def _merge_rects(rects, gap):
    """Une rectángulos [u0, v0, u1, v1, redondo] que se solapan o quedan a menos de `gap`."""
    rects = [list(r) for r in rects]
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                if max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3]) < gap:
                    rects[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), False]
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects


def lid_ports(components, d, margin=0.5, existing=(), min_strip=0.8):
    """Aberturas en la tapa sobre los componentes elegidos (vistos desde arriba).

    `components`: dicts con bbox [x0, y0, z0, x1, y1, z1] y round (de
    cajas_freecad.list_components); `d`: dimensions(). Cada abertura cubre la planta del
    componente más `margin` por lado: circular si el componente es redondo, rectangular
    redondeada (radio ≤ margin, que sigue cubriendo sus esquinas) si no. Las que quedan a
    menos de `min_strip` se unen; los bordes a menos de `min_strip` de la pared se llevan
    hasta ella y nada sale del área interior. Se omiten los componentes ya cubiertos por una
    abertura de tapa existente. Devuelve (aberturas, avisos).
    """
    hx, hy = d["inner_x"]/2, d["inner_y"]/2
    tapa = [port_rect(p, d["seam"]) for p in existing if p.get("side") == "tapa"]
    rects, notes = [], []
    for comp in components:
        x0, y0, _, x1, y1, _ = comp["bbox"]
        # Lo que entra en la pared no cuenta: la abertura de tapa llega como mucho al interior.
        cx0, cy0, cx1, cy1 = max(x0, -hx), max(y0, -hy), min(x1, hx), min(y1, hy)
        if any(r[0] <= cx0 + 1e-6 and r[1] <= cy0 + 1e-6 and r[2] >= cx1 - 1e-6 and r[3] >= cy1 - 1e-6
               for r in tapa):
            notes.append(f"{comp.get('name', 'Componente')}: ya tiene abertura en la tapa.")
            continue
        if x1 < -hx or x0 > hx or y1 < -hy or y0 > hy:
            notes.append(f"{comp.get('name', 'Componente')}: queda fuera de la tapa.")
            continue
        rects.append([x0 - margin, y0 - margin, x1 + margin, y1 + margin, bool(comp.get("round"))])
    ports = []
    for u0, v0, u1, v1, rnd in _merge_rects(rects, min_strip):
        orig = (u0, v0, u1, v1)
        u0, v0, u1, v1 = max(u0, -hx), max(v0, -hy), min(u1, hx), min(v1, hy)
        if hx + u0 < min_strip:
            u0 = -hx
        if hx - u1 < min_strip:
            u1 = hx
        if hy + v0 < min_strip:
            v0 = -hy
        if hy - v1 < min_strip:
            v1 = hy
        w, h = u1 - u0, v1 - v0
        if w <= 0.1 or h <= 0.1:
            continue
        snapped = any(abs(a - b) > 1e-6 for a, b in zip(orig, (u0, v0, u1, v1)))
        rr = round(min(margin, 1.0, min(w, h)/4), 2)
        p = {"side": "tapa", "u": round((u0 + u1)/2, 2), "v": round((v0 + v1)/2, 2),
             "width": math.ceil(w*100)/100, "height": math.ceil(h*100)/100,
             "shape": "redondeada" if rr > 0.05 else "rectangular", "radius": rr, "to_seam": False}
        # El redondeo a centésimas no debe sacar la abertura del área interior.
        p["width"] = min(p["width"], math.floor((hx - abs(p["u"]))*200)/100)
        p["height"] = min(p["height"], math.floor((hy - abs(p["v"]))*200)/100)
        if rnd and not snapped:
            dia = math.ceil(max(w, h)*100)/100
            if abs(p["u"]) + dia/2 <= hx and abs(p["v"]) + dia/2 <= hy:
                p.update(shape="circular", width=dia, height=dia, radius=0.0)
        ports.append(p)
    return ports, notes


def suggested_support_height(c):
    """Altura de soporte (desde el piso) para que el PCB apoye en él."""
    return c["bottom_gap"] + c["pcb_offset"]


def inner_corner_radius(c):
    """Radio de las esquinas interiores (igual que cajas_freecad): con poca holgura
    lateral se reduce para no entrar en la placa; con holgura 0 la esquina es viva."""
    return max(0.0, min(c["radius"] - c["wall"], c["margin"]/_CORNER_FACTOR))


def corner_wall(c):
    """Espesor de pared en la diagonal de la esquina."""
    r, w, ri = c["radius"], c["wall"], inner_corner_radius(c)
    return min(w, math.sqrt(2)*(w + ri - r) + r - ri)


def screw_fit(c, d=None):
    """Igual que cajas_freecad.screw_fit: rosca útil, profundidad del agujero y largos de
    tornillo admisibles (min_len, max_len) para la configuración."""
    d = d or dimensions(c)
    clear, pilot, cb_d, cb_h, csk_d, ins_d, ins_l = SCREWS[c["screw_size"]]
    if c["closure"] == "insertos":
        ins_d = c["screw_hole"] or ins_d
    else:
        pilot = c["screw_hole"] or pilot
    ins_l = c["insert_depth"] or ins_l
    recess = (csk_d - clear)/2 if c["screw_head"] == "avellanada" else cb_h
    grip = d["outer_z"] - d["seam"] - recess
    max_depth = d["seam"] - _SCREW_FLOOR
    if c["closure"] == "insertos":
        hole_d, min_eng = ins_d, 0.75*ins_l
    else:
        hole_d, min_eng = pilot, 2*float(c["screw_size"][1:])
    eng = c["screw_length"] - grip
    depth = eng + 1.0 if c["closure"] == "tornillos" else max(ins_l, eng) + 0.5
    return {"grip": grip, "hole_d": hole_d, "ins_l": ins_l, "eng": eng, "depth": depth,
            "max_depth": max_depth, "min_len": grip + min_eng, "max_len": grip + max_depth - 0.5}


def warnings(c):
    """Avisos (no errores) de una configuración ya validada."""
    out = []
    if c["margin"] <= 0:
        out.append("Holgura lateral 0: la placa queda ajustada contra las paredes (montaje a presión).")
    elif c["margin"] < TIGHT_MARGIN:
        out.append(f"Holgura lateral de {_fmt(c['margin'])} mm: la placa queda muy justa.")
    if c["closure"] in SCREW_CLOSURES:
        f = screw_fit(c)
        out.append(f"Tornillos {c['screw_size']}: agujero en la base Ø{_fmt(f['hole_d'])} mm; sirven de "
                   f"{_fmt(round(f['min_len'], 1))} a {_fmt(round(f['max_len'], 1))} mm de largo.")
    return out


def _err(key, text):
    """Lanza ConfigError con la etiqueta del campo."""
    raise ConfigError(f"{LABELS[key]}: {text}", key)


def validate(c):
    """Valida una configuración normalizada. Lanza ConfigError con mensaje en español."""
    if not c["name"]:
        _err("name", "no puede estar vacío.")
    for key in ("board_x", "board_y", "board_z", "wall", "floor", "lid", "lid_depth"):
        if c[key] <= 0:
            _err(key, "debe ser mayor que cero.")
    for key in ("margin", "bottom_gap", "top_gap", "clearance", "rib_interference", "radius", "pcb_offset"):
        if c[key] < 0:
            _err(key, "no puede ser negativo.")
    if c["pcb_offset"] >= c["board_z"]:
        _err("pcb_offset", f"debe ser menor que {LABELS['board_z']} ({_fmt(c['board_z'])} mm).")
    if c["clearance"] > 0.5:
        _err("clearance", "debe ser como máximo 0,5 mm.")
    if c["rib_interference"] > 0.08:
        _err("rib_interference", "debe ser como máximo 0,08 mm por lado.")
    d = dimensions(c)
    ox, oy, iz = d["outer_x"], d["outer_y"], d["inner_z"]
    skirt = d["lid_skirt"]
    if c["split_height"] < 0:
        _err("split_height", "no puede ser negativa (0 = automática).")
    if c["split_height"] > 0:
        if d["seam"] < c["floor"] + 1 - 1e-9:
            _err("split_height", f"debe ser al menos piso + 1 mm ({_fmt(c['floor'] + 1)} mm).")
        if d["seam"] > d["ceiling"] - 0.5 + 1e-9:
            _err("split_height", f"debe quedar al menos 0,5 mm bajo el techo interior "
                                 f"(máximo {_fmt(round(d['ceiling'] - 0.5, 3))} mm).")
    elif c["lid_depth"] >= iz:
        _err("lid_depth", f"debe ser menor que la altura interior total ({_fmt(iz)} mm).")
    if corner_wall(c) < 0.75*c["wall"] - 1e-9:
        _err("radius", f"es demasiado grande para esta {LABELS['margin'].lower()}: la pared quedaría "
                       f"de {_fmt(round(corner_wall(c), 2))} mm en las esquinas "
                       f"(mínimo {_fmt(0.75*c['wall'])} mm).")
    if c["radius"] >= min(ox, oy)/2:
        _err("radius", "no cabe en la caja.")
    if c["closure"] in LIP_CLOSURES:
        if c["wall"] < 3:
            _err("wall", "los cierres con lengüeta o labio necesitan al menos 3 mm.")
        if not 1 <= c["tongue_height"] <= 4:
            _err("tongue_height", "debe estar entre 1 y 4 mm.")
        if skirt < c["tongue_height"] + 0.5:
            key = "split_height" if c["split_height"] > 0 else "lid_depth"
            _err(key, f"la falda de la tapa (techo − unión = {_fmt(round(skirt, 3))} mm) debe ser al menos "
                      f"{LABELS['tongue_height']} + 0,5 mm ({_fmt(c['tongue_height'] + 0.5)} mm).")
        if d["seam"] - c["floor"] < c["tongue_height"]:
            _err("split_height", "queda demasiado baja para la lengüeta.")
        if c["wall"] < 1.2 + 2*c["clearance"] + 1.2:
            _err("wall", f"con este {LABELS['clearance'].lower()} debe medir al menos "
                         f"{_fmt(2.4 + 2*c['clearance'])} mm.")
        for length in (ox, oy):
            free = length/2 - max(c["radius"] + 2, c["wall"] + 2)
            if (2*free if c["closure"] != "friccion" else min(16, free*0.65)) < 4:
                raise ConfigError("Cierre: la caja es demasiado pequeña para las lengüetas. "
                                  "Usa tapa apoyada o amplía las medidas.", "closure")
    if c["closure"] == "imanes":
        if not 1 <= c["magnet_diameter"] <= 25:
            _err("magnet_diameter", "debe estar entre 1 y 25 mm.")
        if not 0.5 <= c["magnet_height"] <= 10:
            _err("magnet_height", "debe estar entre 0,5 y 10 mm.")
        if not 0 <= c["magnet_clearance"] <= 0.5:
            _err("magnet_clearance", "debe estar entre 0 y 0,5 mm.")
        pocket = c["magnet_height"] + _MAGNET_POCKET_EXTRA
        if d["seam"] < pocket:
            _err("magnet_height", f"no cabe bajo la unión (necesita {_fmt(round(pocket, 2))} mm de base).")
        if skirt + c["lid"] < pocket:
            _err("magnet_height", "no cabe en la tapa: sube el techo o baja la unión.")
    if c["closure"] in SCREW_CLOSURES:
        for key in ("screw_hole", "insert_depth"):
            if c[key] < 0:
                _err(key, "no puede ser negativo (0 = valor típico).")
        f = screw_fit(c, d)
        if f["grip"] < 1.0:
            _err("screw_head", "la tapa es demasiado baja para alojar la cabeza: baja la unión o sube el techo.")
        if c["closure"] == "insertos" and f["ins_l"] + 0.5 > f["max_depth"]:
            _err("insert_depth", "no cabe bajo la unión: sube la unión o usa un inserto más corto.")
        if not f["min_len"] - 1e-9 <= c["screw_length"] or f["depth"] > f["max_depth"] + 1e-9:
            _err("screw_length", f"debe estar entre {_fmt(round(f['min_len'], 1))} y "
                                 f"{_fmt(round(f['max_len'], 1))} mm con esta caja.")
    if c["board_rest"] != "ninguno" and not 0.5 <= c["rest_width"] <= 10:
        _err("rest_width", "debe estar entre 0,5 y 10 mm.")
    # Soportes: dentro del interior (con las esquinas redondeadas) y bajo la unión.
    hx, hy = d["inner_x"]/2, d["inner_y"]/2
    inner_r = inner_corner_radius(c)
    max_h = d["seam"] - c["floor"]
    for i, s in enumerate(c["supports"], 1):
        p = f"Soporte {i}"
        r = s["diameter"]/2
        if s["height"] <= 0:
            raise ConfigError(f"{p}: la altura debe ser mayor que cero.", "supports")
        if s["diameter"] <= 0:
            raise ConfigError(f"{p}: el diámetro debe ser mayor que cero.", "supports")
        if not 0 <= s["hole"] < s["diameter"]:
            raise ConfigError(f"{p}: el agujero debe ser ≥ 0 y menor que el diámetro.", "supports")
        if s["height"] > max_h + 1e-9:
            raise ConfigError(f"{p}: la altura supera el plano de unión de la caja "
                              f"(máximo {_fmt(max_h)} mm).", "supports")
        ax, ay = abs(s["x"]), abs(s["y"])
        outside = ax + r > hx + 1e-9 or ay + r > hy + 1e-9
        if not outside and inner_r > 0:
            cx, cy = hx - inner_r, hy - inner_r
            if ax > cx and ay > cy and math.hypot(ax - cx, ay - cy) + r > inner_r + 1e-9:
                outside = True
        if outside:
            raise ConfigError(f"{p}: queda fuera del espacio interior.", "supports")
    # Aberturas
    total = d["outer_z"]
    for i, port in enumerate(c["ports"], 1):
        p = f"Abertura {i} ({port['side']})"
        if port["width"] <= 0 or port["height"] <= 0:
            raise ConfigError(f"{p}: ancho y alto deben ser mayores que cero.", "ports")
        if port["radius"] < 0:
            raise ConfigError(f"{p}: el radio de las esquinas no puede ser negativo.", "ports")
        if port["to_seam"] and port["side"] != "tapa" and port["v"] - port["height"]/2 >= d["seam"]:
            raise ConfigError(f"{p}: «abrir hasta la unión» necesita que empiece bajo la unión "
                              f"(z = {_fmt(round(d['seam'], 3))} mm).", "ports")
        if port["side"] == "tapa":
            if abs(port["u"]) + port["width"]/2 > d["inner_x"]/2 + 1e-9:
                raise ConfigError(f"{p}: sale del área interior de la tapa (X).", "ports")
            if abs(port["v"]) + port["height"]/2 > d["inner_y"]/2 + 1e-9:
                raise ConfigError(f"{p}: sale del área interior de la tapa (Y).", "ports")
        else:
            w = oy if port["side"] in ("izquierda", "derecha") else ox
            if abs(port["u"]) + port["width"]/2 > w/2 + 1e-9:
                raise ConfigError(f"{p}: sale del ancho de su cara.", "ports")
            if port["v"] - port["height"]/2 < c["floor"] - 1e-9:
                raise ConfigError(f"{p}: invade el piso (el borde inferior debe estar a "
                                  f"≥ {_fmt(c['floor'])} mm).", "ports")
            if port["v"] + port["height"]/2 > total + 1e-9:
                raise ConfigError(f"{p}: supera la altura de la caja ({_fmt(total)} mm).", "ports")
    return c


def config_from(data):
    """Normaliza (copia profunda) y valida. Devuelve un dict nuevo."""
    return validate(normalize(data))


# ---------------------------------------------------------------------------
# Archivos
# ---------------------------------------------------------------------------

def _atomic_write(path, text):
    """Escribe un archivo de texto mediante temporal + reemplazo (nunca queda a medias)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_project(path, config):
    """Guarda el proyecto JSON (UTF-8, escritura atómica). Valida antes de escribir."""
    path = Path(path)
    c = config_from(config)
    if c["step_path"]:
        step = Path(c["step_path"])
        if step.is_absolute():
            try:
                c["step_path"] = step.resolve().relative_to(path.parent.resolve()).as_posix()
            except ValueError:
                c["step_path"] = str(step)
    data = {"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "config": c}
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_project(path, validate_config=True):
    """Lee un proyecto. step_path relativo se resuelve respecto de su carpeta."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigError(f"El proyecto «{path.name}» no está en UTF-8.") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"El proyecto «{path.name}» está dañado (JSON inválido en la "
                          f"línea {exc.lineno}).") from None
    if not isinstance(data, dict) or data.get("format") != PROJECT_FORMAT:
        raise ConfigError(f"«{path.name}» no es un proyecto de Cajas para circuitos.")
    if data.get("version") != PROJECT_VERSION:
        raise ConfigError(f"Versión de proyecto {data.get('version')!r} no compatible "
                          f"(esta aplicación lee la versión {PROJECT_VERSION}).")
    c = data.get("config")
    if not isinstance(c, dict):
        raise ConfigError(f"El proyecto «{path.name}» no contiene una configuración válida.")
    step = str(c.get("step_path") or "").strip()
    if step and not Path(step).is_absolute():
        c["step_path"] = str((path.parent / step).resolve())
    return config_from(c) if validate_config else normalize(c)


def unique_output_dir(parent, name, now=None):
    """Crea y devuelve una carpeta NUEVA <safe_name>_AAAAMMDD_HHMMSS[_n]."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = f"{safe_name(name)}_{stamp}"
    for n in range(1, 10000):
        folder = parent / (base if n == 1 else f"{base}_{n}")
        try:
            folder.mkdir(exist_ok=False)
            return folder
        except FileExistsError:
            continue
    raise ConfigError("No se pudo crear una carpeta de salida nueva.")


_MACRO_HEADER = '''\
# -*- coding: utf-8 -*-
# =============================================================================
# Macro autónoma generada por «Cajas para circuitos» ({fecha}).
# Proyecto: {nombre}
#
# Cómo usarla en FreeCAD 1.0:
#   - Macro > Macros… > elegir este archivo > Ejecutar, o arrastrarlo a la
#     ventana de FreeCAD, o abrirlo y pulsar «Ejecutar macro».
#   - Crea SIEMPRE un documento nuevo; no guarda ni sobrescribe ningún archivo.
#     Guarda el resultado con Archivo > Guardar como.
#   - Para cambiar medidas, edita el diccionario CONFIG al final del archivo y
#     vuelve a ejecutar la macro: se regenera la caja en otro documento nuevo.
#   - Las piezas (base y tapa) son sólidos BREP (Part::Feature), sin historial
#     paramétrico; expórtalas a STL/STEP desde FreeCAD.
#   - No necesita la aplicación «Cajas para circuitos» instalada. Si usa una
#     placa STEP, la ruta de step_path debe existir en este equipo.
# =============================================================================
'''

_MACRO_TAIL = '''

# =============================================================================
# CONFIGURACIÓN (medidas en mm). Edita y vuelve a ejecutar.
# =============================================================================
CONFIG = {config}

try:
    DOCUMENTO, RESULTADO = create_document(CONFIG)
except Exception as _exc:
    App.Console.PrintError("No se pudo crear la caja: " + str(_exc) + "\\n")
    raise
for _aviso in RESULTADO.get("warnings", []):
    App.Console.PrintWarning(_aviso + "\\n")
App.Console.PrintMessage("Caja creada en un documento nuevo. Guárdalo con Archivo > Guardar como.\\n")
'''


def macro_core(core_path=None):
    """Código de cajas_freecad.py anterior al marcador # WORKER_ENTRY."""
    core_path = Path(core_path) if core_path else CORE_FILE
    text = core_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == WORKER_MARKER:
            return "".join(lines[:i]).rstrip() + "\n"
    raise ConfigError(f"No se encontró el marcador «{WORKER_MARKER}» en {core_path.name}; "
                      "no se puede generar la macro.")


def macro_text(config, core_path=None):
    """Texto completo de la macro autónoma para la configuración dada."""
    c = config_from(config)
    config_literal = pprint.pformat(c, indent=1, width=90, sort_dicts=False)
    header = _MACRO_HEADER.format(fecha=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                  nombre=" ".join(c["name"].splitlines()))
    return header + macro_core(core_path) + _MACRO_TAIL.format(config=config_literal)


def write_macro(path, config, overwrite=False, core_path=None):
    """Escribe la macro .FCMacro (UTF-8). Sin overwrite no reemplaza archivos existentes."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Ya existe «{path}». Elige otro nombre o confirma la sobrescritura.")
    text = macro_text(config, core_path)
    _atomic_write(path, text)
    return path

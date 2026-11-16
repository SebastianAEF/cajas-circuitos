"""Protocolo del puente entre la aplicación y la instancia de FreeCAD administrada.

Lo importan los dos lados: la aplicación tkinter (Python normal) y la macro del
puente que corre dentro de FreeCAD. Solo usa la biblioteca estándar.

Funcionamiento: la aplicación crea un directorio privado de sesión con las
subcarpetas `peticiones/` y `respuestas/`. Cada petición es un JSON
`peticiones/<id>.json` y su respuesta es `respuestas/<id>.json`. Los dos lados
escriben con un temporal y `os.replace`, de modo que nunca se lee un archivo a
medias. El puente publica además `latido.json` con su estado. No hay ningún
socket ni servidor de red. Las acciones forman un conjunto cerrado: el texto
recibido nunca se evalúa con eval/exec.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path

PROTOCOLO = 1
ACCIONES = ("estado", "cargar_placa", "calcular", "vista", "exportar", "captura", "detectar", "componentes", "detener")
VISTAS = ("separada", "cerrada", "explosionada")
PIEZAS = ("base", "lid", "board")
PIEZAS_TRANSPARENCIA = ("base", "lid")
RE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RE_CAPTURA = re.compile(r"^[A-Za-z0-9_-]{1,40}\.png$")
RE_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
DIR_PETICIONES = "peticiones"
DIR_RESPUESTAS = "respuestas"
LATIDO = "latido.json"
REGISTRO = "puente.log"
MAX_BYTES_PETICION = 2_000_000

# Tipos de error que puede devolver el puente.
TIPOS_ERROR = ("protocolo", "token", "caducada", "validacion", "estado", "interno")


class ErrorProtocolo(ValueError):
    """Petición mal formada. `tipo` es uno de TIPOS_ERROR."""

    def __init__(self, mensaje, tipo="protocolo"):
        super().__init__(mensaje)
        self.tipo = tipo


def nueva_id():
    """Identificador nuevo de petición."""
    return uuid.uuid4().hex


def nuevo_token():
    """Token secreto de una sesión del visor."""
    return uuid.uuid4().hex + uuid.uuid4().hex


def escribir_json_atomico(ruta, datos, intentos=20):
    """Escribe con temporal + os.replace. En Windows, replace falla con PermissionError si
    el otro lado tiene el destino abierto en ese instante: se reintenta brevemente y el
    temporal nunca queda suelto."""
    ruta = Path(ruta)
    tmp = ruta.with_name(f".{ruta.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        for i in range(intentos):
            try:
                os.replace(tmp, ruta)
                return
            except PermissionError:
                if i == intentos - 1:
                    raise
                time.sleep(0.01)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def leer_json(ruta, intentos=1):
    """Lee un JSON. Con intentos > 1 reintenta ante PermissionError (archivo reemplazándose)."""
    ruta = Path(ruta)
    for i in range(intentos):
        try:
            if ruta.stat().st_size > MAX_BYTES_PETICION:
                raise ErrorProtocolo("El mensaje supera el tamaño máximo permitido.")
            return json.loads(ruta.read_text(encoding="utf-8"))
        except PermissionError:
            if i == intentos - 1:
                raise
            time.sleep(0.01)


def preparar_directorio(base):
    """Crea las carpetas de peticiones y respuestas de la sesión."""
    base = Path(base)
    (base / DIR_PETICIONES).mkdir(parents=True, exist_ok=True)
    (base / DIR_RESPUESTAS).mkdir(parents=True, exist_ok=True)
    return base


def ruta_peticion(base, id_):
    """Ruta del archivo de una petición."""
    return Path(base) / DIR_PETICIONES / f"{id_}.json"


def ruta_respuesta(base, id_):
    """Ruta del archivo de una respuesta."""
    return Path(base) / DIR_RESPUESTAS / f"{id_}.json"


def peticion(token, accion, datos=None, id_=None, plazo=None):
    """Arma una petición. `plazo`: segundos tras los cuales el puente la descarta."""
    p = {"protocolo": PROTOCOLO, "token": token, "id": id_ or nueva_id(), "accion": accion,
         "datos": datos or {}, "creada": time.time()}
    if plazo is not None:
        p["caduca"] = p["creada"] + float(plazo)
    return p


def respuesta(token, id_, ok, resultado=None, error=None, tipo=None, duracion=0.0):
    """Arma la respuesta del puente (resultado o error con su tipo)."""
    r = {"protocolo": PROTOCOLO, "token": token, "id": id_, "ok": bool(ok),
         "duracion": round(float(duracion), 4)}
    if ok:
        r["resultado"] = resultado if resultado is not None else {}
    else:
        r["error"] = str(error or "Error desconocido.")
        r["tipo_error"] = tipo if tipo in TIPOS_ERROR else "interno"
    return r


def _bool(valor, nombre):
    """Exige un booleano JSON (no acepta 0/1 ni textos)."""
    if not isinstance(valor, bool):
        raise ErrorProtocolo(f"«{nombre}» debe ser verdadero o falso.", "validacion")
    return valor


def _config(datos):
    """Configuración de la caja dentro de los datos de una petición."""
    c = datos.get("config")
    if not isinstance(c, dict):
        raise ErrorProtocolo("Falta la configuración de la caja («config»).", "validacion")
    return c


def _validar_vista(datos, salida):
    """Valida vista, visibilidad, transparencias, encuadre y colores de una petición."""
    if "vista" in datos:
        if datos["vista"] not in VISTAS:
            raise ErrorProtocolo("Vista no reconocida: " + str(datos["vista"]), "validacion")
        salida["vista"] = datos["vista"]
    if "visible" in datos:
        v = datos["visible"]
        if not isinstance(v, dict) or not set(v) <= set(PIEZAS):
            raise ErrorProtocolo("«visible» debe indicar base, lid y/o board.", "validacion")
        salida["visible"] = {k: _bool(x, "visible." + k) for k, x in v.items()}
    if "transparencia" in datos:
        t = datos["transparencia"]
        if not isinstance(t, dict) or not set(t) <= set(PIEZAS_TRANSPARENCIA):
            raise ErrorProtocolo("«transparencia» solo admite base y lid.", "validacion")
        out = {}
        for k, x in t.items():
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 100:
                raise ErrorProtocolo("La transparencia debe estar entre 0 y 100.", "validacion")
            out[k] = int(round(x))
        salida["transparencia"] = out
    if "encuadrar" in datos:
        salida["encuadrar"] = _bool(datos["encuadrar"], "encuadrar")
    if "colores" in datos:
        col = datos["colores"]
        if not isinstance(col, dict) or not set(col) <= set(PIEZAS) | {"step"}:
            raise ErrorProtocolo("«colores» solo admite base, lid, board y step.", "validacion")
        out = {}
        for k, x in col.items():
            if k == "step":
                out[k] = _bool(x, "colores.step")
            elif not isinstance(x, str) or not RE_COLOR.match(x):
                raise ErrorProtocolo("Los colores deben ser #RRGGBB.", "validacion")
            else:
                out[k] = x
        salida["colores"] = out
    return salida


def validar_peticion(p, token, ahora=None):
    """Comprueba una petición recibida. Devuelve (id, accion, datos) normalizados.

    Lanza ErrorProtocolo con el tipo adecuado. Las claves desconocidas de `datos`
    se descartan.
    """
    if not isinstance(p, dict):
        raise ErrorProtocolo("La petición no es un objeto JSON.")
    id_ = p.get("id")
    if not isinstance(id_, str) or not RE_ID.match(id_):
        raise ErrorProtocolo("Identificador de petición no válido.")
    if p.get("protocolo") != PROTOCOLO:
        raise ErrorProtocolo(f"Versión de protocolo incompatible (se espera {PROTOCOLO}).")
    if not isinstance(p.get("token"), str) or not hmac.compare_digest(p["token"], token):
        raise ErrorProtocolo("La petición no pertenece a esta sesión.", "token")
    accion = p.get("accion")
    if accion not in ACCIONES:
        raise ErrorProtocolo("Acción no permitida: " + str(accion)[:40])
    caduca = p.get("caduca")
    if caduca is not None:
        if isinstance(caduca, bool) or not isinstance(caduca, (int, float)):
            raise ErrorProtocolo("«caduca» debe ser un número.")
        if (ahora if ahora is not None else time.time()) > caduca:
            raise ErrorProtocolo("La petición venció antes de procesarse.", "caducada")
    datos = p.get("datos", {})
    if not isinstance(datos, dict):
        raise ErrorProtocolo("«datos» debe ser un objeto.")

    limpio = {}
    if accion == "cargar_placa":
        c = _config(datos)
        if not isinstance(c.get("step_path"), str) or not c["step_path"].strip():
            raise ErrorProtocolo("Indica el archivo STEP de la placa.", "validacion")
        limpio["config"] = c
        _validar_vista(datos, limpio)
    elif accion == "calcular":
        limpio["config"] = _config(datos)
        _validar_vista(datos, limpio)
    elif accion == "vista":
        _validar_vista(datos, limpio)
    elif accion == "detectar":
        limpio["config"] = _config(datos)
        m = datos.get("margen", 0.5)
        if isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 <= m <= 5:
            raise ErrorProtocolo("«margen» debe ser un número entre 0 y 5 mm.", "validacion")
        limpio["margen"] = float(m)
        limpio["hasta_union"] = _bool(datos.get("hasta_union", False), "hasta_union")
    elif accion == "componentes":
        limpio["config"] = _config(datos)
    elif accion == "exportar":
        limpio["config"] = _config(datos)
        carpeta = datos.get("carpeta")
        if not isinstance(carpeta, str) or not os.path.isabs(carpeta):
            raise ErrorProtocolo("La carpeta de exportación debe ser una ruta absoluta.", "validacion")
        limpio["carpeta"] = carpeta
    elif accion == "captura":
        archivo = datos.get("archivo", "captura.png")
        if not isinstance(archivo, str) or not RE_CAPTURA.match(archivo):
            raise ErrorProtocolo("Nombre de captura no válido (solo letras, números, - y _ con .png).",
                                 "validacion")
        limpio["archivo"] = archivo
        for k, defecto in (("ancho", 1200), ("alto", 900)):
            x = datos.get(k, defecto)
            if isinstance(x, bool) or not isinstance(x, int) or not 200 <= x <= 4000:
                raise ErrorProtocolo(f"«{k}» debe ser un entero entre 200 y 4000.", "validacion")
            limpio[k] = x
    return id_, accion, limpio


def proceso_vivo(pid):
    """True si el proceso existe. En Windows no usa os.kill (que lo terminaría)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # acceso denegado: existe
        try:
            codigo = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(codigo)):
                return False
            return codigo.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

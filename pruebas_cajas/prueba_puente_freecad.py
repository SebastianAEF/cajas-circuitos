"""Prueba de integración del puente con FreeCAD GUI REAL (SesionFreeCAD directo).

Ejecutar con el Python normal (NO el de FreeCAD), desde cualquier carpeta:
    python pruebas_cajas\\prueba_puente_freecad.py [filtro ...]

Variables opcionales:
    CAJAS_FREECAD  ruta de freecad.exe   (por defecto C:\\Program Files\\FreeCAD 1.0\\bin\\freecad.exe)
    CAJAS_STEP     placa STEP de prueba  (solo lectura; se trabaja sobre copias)
    CAJAS_PRUEBA_SALIDA  carpeta donde se copian capturas y exportaciones

Lanza sus propias instancias de FreeCAD y SOLO termina procesos cuyo PID lanzó
ella misma. Nunca toca otros FreeCAD abiertos (los comprueba al final). Imprime
un resumen PASS/FAIL/XFAIL y una tabla de tiempos; sale con código 1 si algo falla.
"""
import ctypes
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cajas_modelo as M  # noqa: E402
import cajas_protocolo as P  # noqa: E402
import cajas_sesion_freecad as S  # noqa: E402

FREECAD = Path(os.environ.get("CAJAS_FREECAD", r"C:\Program Files\FreeCAD 1.0\bin\freecad.exe"))
FREECAD_PY = FREECAD.with_name("python.exe")
STEP = os.environ.get("CAJAS_STEP", r"C:\Users\tupin\Downloads\TEC-8A-24V-PID-HC Step File.stp")
SALIDA = Path(os.environ.get("CAJAS_PRUEBA_SALIDA") or
              Path(tempfile.gettempdir()) / ("prueba_puente_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
TMP = SALIDA / "tmp ñandú"            # datos de trabajo (con espacios y acentos)
CAPTURAS = SALIDA / "capturas"

RESULTADOS = []      # (nombre, estado, detalle)
TIEMPOS = []         # (operación, segundos de pared, tiempos del puente, read_count)
MIS_PIDS = set()     # PIDs de visores lanzados por esta prueba
REGISTRO = []
COMPARTIDO = {}
SESION = None


class XFail(Exception):
    """Fallo conocido de un archivo ajeno, documentado."""


def registro(texto):
    REGISTRO.append(f"{time.strftime('%H:%M:%S')} {texto}")
    print("    [sesión] " + texto, flush=True)


def run(nombre, fn):
    if sys.argv[1:] and not any(a.lower() in nombre.lower() for a in sys.argv[1:]):
        return
    t = time.time()
    try:
        detalle = fn() or ""
        estado = "PASS"
    except XFail as ex:
        estado, detalle = "XFAIL", str(ex)
    except Exception as ex:
        estado = "FAIL"
        detalle = f"{type(ex).__name__}: {ex}\n{traceback.format_exc()}"
    dt = time.time() - t
    RESULTADOS.append((nombre, estado, detalle))
    print(f"{estado:5s} {nombre} ({dt:.1f} s) {detalle if estado != 'FAIL' else ''}", flush=True)
    if estado == "FAIL":
        print(detalle, flush=True)


def check(cond, texto):
    if not cond:
        raise AssertionError(texto)


def error_de(fn, *a, **k):
    try:
        fn(*a, **k)
    except S.ErrorSesion as ex:
        return ex
    raise AssertionError("se esperaba ErrorSesion")


def sol(s, accion, datos=None, plazo=300.0, etiqueta=None):
    t = time.time()
    r = s.solicitar(accion, datos, plazo=plazo)
    dt = time.time() - t
    if etiqueta:
        rc = (r.get("cache") or {}).get("read_count")
        TIEMPOS.append((etiqueta, dt, r.get("tiempos"), rc))
    return r, dt


def registrar_pid(s):
    if s.proc is not None:
        MIS_PIDS.add(s.proc.pid)


# ------------------------------------------------------------ procesos (solo propios)
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.OpenProcess.restype = wintypes.HANDLE
K32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
K32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
K32.CloseHandle.argtypes = (wintypes.HANDLE,)
NTDLL = ctypes.WinDLL("ntdll")
NTDLL.NtSuspendProcess.argtypes = (wintypes.HANDLE,)


def suspender(pid):
    """Congela un proceso PROPIO con NtSuspendProcess (simula un visor colgado)."""
    assert pid in MIS_PIDS, "solo se suspenden procesos propios"
    h = K32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    if not h:
        raise OSError(ctypes.get_last_error(), "OpenProcess")
    try:
        st = NTDLL.NtSuspendProcess(h)
        if st != 0:
            raise OSError(st, "NtSuspendProcess")
    finally:
        K32.CloseHandle(h)


def terminar_pid_propio(pid):
    assert pid in MIS_PIDS, "solo se terminan procesos propios"
    h = K32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        try:
            K32.TerminateProcess(h, 1)
        finally:
            K32.CloseHandle(h)


def pids_freecad():
    """PIDs de freecad.exe en ejecución (solo lectura, con tasklist)."""
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq freecad.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    pids = set()
    for linea in out.splitlines():
        partes = [x.strip('"') for x in linea.split('","')]
        if len(partes) > 1 and partes[0].lower().startswith("freecad"):
            try:
                pids.add(int(partes[1]))
            except ValueError:
                pass
    return pids


def comprobar_estable(s, intentos=5):
    """comprobar() reintentando «iniciando»: en Windows una lectura de latido.json
    que coincide con el os.replace del puente falla de forma transitoria."""
    for _ in range(intentos):
        e = s.comprobar()
        if e != "iniciando":
            return e
        time.sleep(0.02)
    return e


def esperar(cond, plazo, paso=0.2):
    t0 = time.time()
    while time.time() - t0 < plazo:
        if cond():
            return True
        time.sleep(paso)
    return cond()


def config(**kw):
    c = dict(name="Prueba puente", step_path=STEP, up_axis="Z", rotation=0.0,
             export_stl=False, export_step=False)
    c.update(kw)
    return c


def guardar_captura(s, nombre, min_bytes=1000, **vista):
    if vista:
        sol(s, "vista", vista, plazo=60)
    r, _ = sol(s, "captura", {"archivo": nombre + ".png", "ancho": 1000, "alto": 750}, plazo=60)
    origen = Path(r["archivo"])
    check(origen.is_file() and origen.stat().st_size > min_bytes, f"captura vacía: {origen}")
    CAPTURAS.mkdir(parents=True, exist_ok=True)
    destino = CAPTURAS / origen.name
    shutil.copyfile(origen, destino)
    COMPARTIDO.setdefault("capturas", []).append(str(destino))
    return r, destino


# =========================================================================== casos
def a_arranque():
    s = SESION
    t = time.time()
    modo = s.iniciar()
    t_ini = time.time() - t
    registrar_pid(s)
    check(modo == "nueva", modo)
    TIEMPOS.append(("arranque del visor (iniciar)", t_ini, None, None))
    r, _ = sol(s, "estado", plazo=30)
    check(r["gui"] is True and r["pid"] == s.proc.pid, r)
    check(r["version"].startswith("1.0"), r["version"])
    check(comprobar_estable(s) == "conectada", s.comprobar())
    return f"iniciar {t_ini:.1f} s; FreeCAD {r['version']}; pid {r['pid']}"


def a_carga_y_recalculos():
    s = SESION
    r, t_carga = sol(s, "cargar_placa", {"config": config()}, etiqueta="cargar_placa (Part.read)")
    check(r["lectura_nueva"] is True and r["cache"]["read_count"] == 1, r)
    check(abs(r["size"][0] - 44) < 0.05 and abs(r["size"][1] - 59) < 0.05, r["size"])
    variantes = [
        ("calcular base", {}),
        ("calcular holgura 0,25", dict(clearance=0.25)),
        ("calcular pared 3,5", dict(wall=3.5)),
        ("calcular + abertura", dict(wall=3.5, ports=[{"side": "frente", "u": 0, "v": 9,
                                                        "width": 10, "height": 5}])),
        ("calcular + soporte", dict(wall=3.5, ports=[{"side": "frente", "u": 0, "v": 9, "width": 10,
                                                       "height": 5}],
                                    supports=[{"x": -14, "y": -20, "height": 2.0, "diameter": 3,
                                               "hole": 0}])),
    ]
    tiempos = []
    for etiqueta, kw in variantes:
        r, dt = sol(s, "calcular", {"config": config(**kw)}, etiqueta=etiqueta)
        tiempos.append(dt)
        check(r["lectura_nueva"] is False, (etiqueta, r["lectura_nueva"]))
        check(r["cache"]["read_count"] == 1, (etiqueta, r["cache"]))
        check(r["board_reassigned"] is False, (etiqueta, "board_reassigned", r["sync"]))
        check(abs(r["size"][2] - 18.805) < 0.01, r["size"])
    check(max(tiempos) < 0.5 * t_carga, f"recálculos {tiempos} no mucho menores que la carga {t_carga:.2f}")
    COMPARTIDO["t_carga"] = t_carga
    return f"carga {t_carga:.2f} s; recálculos " + ", ".join(f"{x:.2f}" for x in tiempos) + " s"


def b_orientacion():
    s = SESION
    r, _ = sol(s, "calcular", {"config": config(up_axis="X")}, etiqueta="calcular eje X")
    check(r["lectura_nueva"] is False and r["cache"]["read_count"] == 1, r["cache"])
    check(r["board_reassigned"] is True, r["sync"])
    check(abs(r["size"][2] - 44) < 0.05, f"con eje X Z debería ser ≈44: {r['size']}")
    tx = r["size"]
    r, _ = sol(s, "calcular", {"config": config(up_axis="Z", rotation=90)}, etiqueta="calcular Z rot 90")
    check(r["board_reassigned"] is True and r["cache"]["read_count"] == 1, r["cache"])
    check(abs(r["size"][0] - 59) < 0.05 and abs(r["size"][1] - 44) < 0.05, r["size"])
    r, _ = sol(s, "calcular", {"config": config()}, etiqueta="calcular vuelta a Z")
    check(r["board_reassigned"] is True and r["lectura_nueva"] is False and r["cache"]["read_count"] == 1, r)
    check(abs(r["size"][2] - 18.805) < 0.01, r["size"])
    return f"X: {[round(x, 2) for x in tx]}; Z: {[round(x, 2) for x in r['size']]}; cache {r['cache']}"


def c_cambio_en_disco():
    s = SESION
    carpeta = TMP / "Prueba puente ñandú"
    carpeta.mkdir(parents=True, exist_ok=True)
    copia = carpeta / "placa uno.stp"
    shutil.copyfile(STEP, copia)
    cfg = config(step_path=str(copia))
    r, _ = sol(s, "cargar_placa", {"config": cfg}, etiqueta="cargar_placa copia (ruta ñandú)")
    rc0 = r["cache"]["read_count"]
    check(r["lectura_nueva"] is True and rc0 == 2, r["cache"])
    r, _ = sol(s, "calcular", {"config": cfg}, etiqueta="calcular copia")
    check(r["lectura_nueva"] is False and r["cache"]["read_count"] == rc0, r["cache"])
    st = copia.stat()
    os.utime(copia, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    r, _ = sol(s, "calcular", {"config": cfg}, etiqueta="calcular tras cambiar mtime")
    check(r["lectura_nueva"] is True and r["cache"]["read_count"] == rc0 + 1, r["cache"])
    check(r["board_reassigned"] is True, "la placa cambió de clave y debería reasignarse")
    r, _ = sol(s, "calcular", {"config": cfg}, etiqueta="calcular sin cambios")
    check(r["lectura_nueva"] is False and r["cache"]["read_count"] == rc0 + 1, r["cache"])
    check(r["board_reassigned"] is False, r["sync"])
    return f"read_count {rc0} → {r['cache']['read_count']}; cache {r['cache']}"


def d_vistas():
    s = SESION
    r, _ = sol(s, "calcular", {"config": config(wall=3.5), "vista": "separada",
                               "visible": {"base": True, "lid": True, "board": True},
                               "transparencia": {"base": 0, "lid": 0}, "encuadrar": True},
               etiqueta="calcular (vuelta al STEP original)")
    check(r["lectura_nueva"] is False, "el STEP original seguía en caché")
    vistos = {}
    todas = {"CajaBase", "CajaTapa", "CajaPlaca"}
    for nombre, vista, esperadas in (
            ("1_separada", {"vista": "separada"}, todas),
            ("2_cerrada", {"vista": "cerrada"}, todas),
            ("3_explosionada", {"vista": "explosionada"}, todas),
            ("4_tapa_transparente", {"vista": "explosionada", "transparencia": {"lid": 70}}, todas),
            ("5_sin_base_ni_tapa", {"vista": "separada", "visible": {"base": False, "lid": False},
                                    "transparencia": {"lid": 0}}, {"CajaPlaca"}),
            ("6_sin_placa", {"vista": "separada", "visible": {"base": True, "lid": True, "board": False}},
             {"CajaBase", "CajaTapa"})):
        rc, destino = guardar_captura(s, nombre, **vista)
        vis = set(rc["visibles"]) & todas
        check(vis == esperadas, f"{nombre}: visibles {rc['visibles']}")
        check(todas <= set(rc["objetos"]), rc["objetos"])
        vistos[nombre] = sorted(vis)
    sol(s, "vista", {"vista": "cerrada", "visible": {"base": True, "lid": True, "board": True}})
    return "capturas en " + str(CAPTURAS) + "; " + "; ".join(f"{k}: {v}" for k, v in vistos.items())


def e_exportar():
    s = SESION
    cfg = config(wall=3.5, export_stl=True, export_step=True, name="Caja ñandú prueba")
    r, _ = sol(s, "calcular", {"config": cfg}, etiqueta="calcular para exportar")
    padre = TMP / "Exportación prueba ñandú"
    carpeta = M.unique_output_dir(padre, cfg["name"])
    r, _ = sol(s, "exportar", {"config": cfg, "carpeta": str(carpeta)}, etiqueta="exportar (STL+STEP)")
    check(r["caja_reutilizada"] is True, "caja_reutilizada debería ser True")
    check(r["lectura_nueva"] is False, r["cache"])
    archivos = [Path(x) for x in r["files"]]
    nombres = sorted(a.name for a in archivos)
    check(nombres == sorted(["Caja.FCStd", "Base.stl", "Tapa.stl", "Base.step", "Tapa.step"]), nombres)
    for a in archivos:
        check(a.is_file() and a.stat().st_size > 0, f"vacío o ausente: {a}")
        check(a.parent == carpeta, a)
    mtimes = {a.name: a.stat().st_mtime_ns for a in carpeta.iterdir()}
    # Otra vez a la misma carpeta: error de validación sin sobrescribir.
    ex = error_de(s.solicitar, "exportar", {"config": cfg, "carpeta": str(carpeta)}, plazo=120)
    check(ex.tipo == "validacion", (ex.tipo, str(ex)))
    check({a.name: a.stat().st_mtime_ns for a in carpeta.iterdir()} == mtimes, "se modificaron archivos")
    # Configuración distinta: no reutiliza la caja.
    carpeta2 = M.unique_output_dir(padre, cfg["name"])
    r2, _ = sol(s, "exportar", {"config": dict(cfg, clearance=0.3, export_step=False),
                                "carpeta": str(carpeta2)}, etiqueta="exportar (config distinta)")
    check(r2["caja_reutilizada"] is False and r2["lectura_nueva"] is False, r2)
    # El FCStd se reabre con el Python de FreeCAD sin GUI.
    script = TMP / "reabrir_fcstd.py"
    script.write_text(
        "import sys, json\nimport FreeCAD\n"
        "d = FreeCAD.openDocument(sys.argv[1])\n"
        "out = {o.Name: (o.Shape.isValid() and o.Shape.Volume > 0) for o in d.Objects "
        "if hasattr(o, 'Shape') and not o.Shape.isNull()}\n"
        "print('RESULTADO ' + json.dumps({'objetos': [o.Name for o in d.Objects], 'solidos': out}))\n",
        encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    p = subprocess.run([str(FREECAD_PY), str(script), str(carpeta / "Caja.FCStd")], capture_output=True,
                       timeout=180, env=env, encoding="utf-8", errors="replace")
    linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    check(p.returncode == 0 and linea, f"no se pudo reabrir: rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    info = json.loads(linea[len("RESULTADO "):])
    check(info["solidos"] and all(info["solidos"].values()), info)
    COMPARTIDO["exportacion"] = str(carpeta)
    tam = {a.name: a.stat().st_size for a in archivos}
    return f"{carpeta}; tamaños {tam}; FCStd reabierto: {info['objetos']}"


def _esperar_respuesta(s, id_, plazo=15):
    ruta = P.ruta_respuesta(s.dir, id_)
    check(esperar(ruta.is_file, plazo, 0.05), f"sin respuesta para {id_}")
    r = P.leer_json(ruta)
    ruta.unlink()
    return r


def f_errores_protocolo():
    s = SESION
    pet_dir = Path(s.dir) / P.DIR_PETICIONES
    casos = []
    # JSON corrupto con id válido
    id_ = P.nueva_id()
    tmp = pet_dir / f".{id_}.tmp"
    tmp.write_text("{esto no es json", encoding="utf-8")
    os.replace(tmp, pet_dir / f"{id_}.json")
    casos.append(("json corrupto", id_, "protocolo"))
    for etiqueta, pet, tipo in (
            ("token incorrecto", P.peticion("x" * 64, "estado"), "token"),
            ("acción no permitida", P.peticion(s.token, "__import__('os').system('calc')"), "protocolo"),
            ("caducada", P.peticion(s.token, "estado", plazo=-1), "caducada"),
            ("captura con ruta", P.peticion(s.token, "captura", {"archivo": "..\\a.png"}), "validacion")):
        P.escribir_json_atomico(P.ruta_peticion(s.dir, pet["id"]), pet)
        casos.append((etiqueta, pet["id"], tipo))
    detalles = []
    for etiqueta, id_, tipo in casos:
        r = _esperar_respuesta(s, id_)
        check(r["ok"] is False and r["tipo_error"] == tipo, (etiqueta, r))
        check(r["token"] == s.token, "la respuesta debe llevar el token de la sesión")
        detalles.append(f"{etiqueta}→{r['tipo_error']}")
    check(not any(pet_dir.iterdir()), "quedaron peticiones sin atender")
    r, _ = sol(s, "estado", plazo=30)
    check(r["gui"], r)
    # Configuración inválida
    ex = error_de(s.solicitar, "calcular", {"config": config(wall=1)}, plazo=60)
    check(ex.tipo == "validacion", (ex.tipo, str(ex)))
    detalles.append(f"pared 1 mm→{ex.tipo} («{ex}»)")
    ex = error_de(s.solicitar, "cargar_placa", {"config": config(step_path=str(TMP / "no existe.stp"))},
                  plazo=60)
    check(ex.tipo == "validacion", (ex.tipo, str(ex)))
    detalles.append(f"STEP inexistente→{ex.tipo}")
    r, _ = sol(s, "estado", plazo=30)
    return "; ".join(detalles) + f"; el puente sigue atendiendo (atendidas={r['atendidas']})"


def g_concurrencia():
    s = SESION
    caja = {}

    def calcular():
        try:
            caja["r"] = s.solicitar("calcular", {"config": config(wall=3.5, clearance=0.22,
                                                                  top_gap=4.0)}, plazo=300)
        except Exception as ex:  # noqa: BLE001
            caja["e"] = ex
    t = threading.Thread(target=calcular, daemon=True)
    t.start()
    check(esperar(lambda: s.ocupada, 5, 0.002), "el cálculo no empezó")
    ex = error_de(s.solicitar, "estado", plazo=10)
    check(ex.tipo == "ocupado", ex.tipo)
    estados = set()
    while t.is_alive():
        estados.add(s.comprobar())
        time.sleep(0.05)
    t.join()
    check("r" in caja, caja.get("e"))
    check(estados and estados <= {"ocupada", "iniciando"} and "ocupada" in estados, estados)
    check(comprobar_estable(s) == "conectada", s.comprobar())
    return f"segunda petición → {ex.tipo}; comprobar() durante el cálculo: {sorted(estados)}"


def h1_matar_visor():
    s = SESION
    pid = s.proc.pid
    s.proc.kill()
    s.proc.wait(timeout=20)
    check(not P.proceso_vivo(pid), "el visor sigue vivo")
    check(s.comprobar() == "cerrada", s.comprobar())
    ex = error_de(s.solicitar, "estado", plazo=10)
    if ex.tipo != "cerrada":
        raise XFail(f"solicitar() con el visor muerto da «{ex.tipo}» y no «cerrada»: "
                    "cajas_sesion_freecad.solicitar comprueba `not self.vivo()` antes de enviar y lanza "
                    "sin_sesion, sin distinguir un visor que se cerró de uno nunca iniciado "
                    "(comprobar() sí da «cerrada»).")
    return f"pid {pid} terminado; comprobar()=cerrada; solicitar→{ex.tipo}"


def h2_relanzar():
    s = SESION
    n = s.lanzamientos
    t = time.time()
    modo = s.iniciar()
    registrar_pid(s)
    dt = time.time() - t
    TIEMPOS.append(("iniciar tras cerrar el visor", dt, None, None))
    check(modo == "nueva" and s.lanzamientos == n + 1, (modo, s.lanzamientos, n))
    r, _ = sol(s, "estado", plazo=30)
    check(r["pid"] == s.proc.pid and r["hay_caja"] is False and r["cache"]["read_count"] == 0, r)
    return f"nuevo pid {s.proc.pid} en {dt:.1f} s; lanzamientos {s.lanzamientos}"


def f3_vista_antes_de_calcular():
    s = SESION
    r, _ = sol(s, "vista", {"vista": "explosionada", "visible": {"board": False}, "encuadrar": True},
               plazo=60)
    check(r["hay_caja"] is False, r)
    rc, destino = guardar_captura(s, "7_sesion_nueva_vacia", min_bytes=100)
    r, _ = sol(s, "estado", plazo=30)
    check(r["hay_caja"] is False, r)
    # Y el flujo normal sigue funcionando en la sesión nueva.
    r, _ = sol(s, "cargar_placa", {"config": config()}, etiqueta="cargar_placa (sesión nueva)")
    r, _ = sol(s, "calcular", {"config": config()}, etiqueta="calcular (sesión nueva)")
    check(r["cache"]["read_count"] == 1, r["cache"])
    return f"vista sin caja → hay_caja False; objetos {rc['objetos']}; luego carga+cálculo OK"


def h3_reutilizar():
    s = SESION
    n, pid = s.lanzamientos, s.proc.pid
    modo = s.iniciar()
    check(modo == "reutilizada" and s.lanzamientos == n and s.proc.pid == pid, (modo, s.lanzamientos))
    return f"reutilizada, pid {pid}, lanzamientos {n}"


def h4_visor_colgado():
    s = SESION
    viejo = s.proc.pid
    n = s.lanzamientos
    suspender(viejo)
    t = time.time()
    modo = s.iniciar()
    dt = time.time() - t
    registrar_pid(s)
    nuevo = s.proc.pid
    check(modo == "nueva" and s.lanzamientos == n + 1, (modo, s.lanzamientos))
    check(nuevo != viejo, "mismo pid")
    check(esperar(lambda: not P.proceso_vivo(viejo), 10), f"el visor colgado {viejo} sigue vivo")
    check(P.proceso_vivo(nuevo), "el visor nuevo no está vivo")
    vivos = [p for p in MIS_PIDS if P.proceso_vivo(p)]
    check(vivos == [nuevo], f"procesos propios vivos: {vivos}")
    r, _ = sol(s, "estado", plazo=30)
    check(r["pid"] == nuevo, r)
    TIEMPOS.append(("iniciar con visor colgado (detección + relanzar)", dt, None, None))
    return (f"suspendido {viejo} con NtSuspendProcess; iniciar() lo terminó y lanzó {nuevo} en {dt:.1f} s; "
            f"vivos propios: {vivos}")


def h5_detener():
    s = SESION
    pid, carpeta = s.proc.pid, s.dir
    REGISTRO.clear()
    t = time.time()
    s.detener()
    dt = time.time() - t
    check(not P.proceso_vivo(pid), "el visor sigue vivo")
    check(not any("no se cerró a tiempo" in x for x in REGISTRO), "detener tuvo que matar el proceso")
    check(not Path(carpeta).exists(), f"no se borró {carpeta}")
    check(s.proc is None and s.dir is None and s.comprobar() == "detenida", s.comprobar())
    return f"cierre ordenado en {dt:.1f} s; directorio de sesión borrado"


HIJO = r'''
import sys
sys.path.insert(0, sys.argv[1])
import cajas_sesion_freecad as S
s = S.SesionFreeCAD(sys.argv[2])
s.iniciar()
print("PID", s.proc.pid, flush=True)
print("DIR", s.dir, flush=True)
# Sale SIN detener la sesión.
'''


def j_pid_padre():
    script = TMP / "hijo_sesion.py"
    script.write_text(HIJO, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(script), str(ROOT), str(FREECAD)], capture_output=True,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    t_fin = time.time()
    lineas = dict(x.split(" ", 1) for x in p.stdout.splitlines() if x[:4] in ("PID ", "DIR "))
    check(p.returncode == 0 and "PID" in lineas, f"rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    pid = int(lineas["PID"])
    MIS_PIDS.add(pid)
    try:
        cerro = esperar(lambda: not P.proceso_vivo(pid), 30, 0.1)
        dt = time.time() - t_fin
        if not cerro:
            terminar_pid_propio(pid)
        check(cerro, f"el visor {pid} no se cerró solo en 30 s")
        check(dt <= 15, f"tardó {dt:.1f} s (se esperaba ≈10 s o menos)")
    finally:
        if lineas.get("DIR") and Path(lineas["DIR"]).name.startswith("cajas_sesion_"):
            log = Path(lineas["DIR"]) / P.REGISTRO
            COMPARTIDO["log_hijo"] = log.read_text(encoding="utf-8") if log.is_file() else ""
            shutil.rmtree(lineas["DIR"], ignore_errors=True)
    TIEMPOS.append(("cierre del visor tras morir la app (PID padre)", dt, None, None))
    return f"visor {pid} se cerró solo {dt:.1f} s después de que salió el proceso padre"


# =========================================================================== principal
def main():
    global SESION
    for req, nombre in ((FREECAD, "freecad.exe"), (Path(STEP), "STEP de prueba")):
        if not Path(req).is_file():
            print(f"No se encuentra {nombre}: {req}")
            return 2
    TMP.mkdir(parents=True, exist_ok=True)
    ajenos = pids_freecad()
    print(f"FreeCAD ajenos al inicio (no se tocan): {sorted(ajenos)}")
    print(f"Salida: {SALIDA}\n", flush=True)
    SESION = S.SesionFreeCAD(FREECAD, registro=registro)
    try:
        run("a1 arranque y estado", a_arranque)
        if SESION.vivo():
            run("a2 carga STEP y recálculos con caché", a_carga_y_recalculos)
            run("b orientación sin releer", b_orientacion)
            run("c cambio en disco (ruta con ñ y espacios)", c_cambio_en_disco)
            run("d vistas, visibilidad y capturas", d_vistas)
            run("e exportar", e_exportar)
            run("f1 errores de protocolo y validación", f_errores_protocolo)
            run("g concurrencia", g_concurrencia)
            run("h1 matar el visor (= cerrar la ventana)", h1_matar_visor)
            run("h2 iniciar tras cierre", h2_relanzar)
            run("f3 vista antes de calcular (sesión nueva)", f3_vista_antes_de_calcular)
            run("h3 iniciar con visor vivo (reutilizar)", h3_reutilizar)
            run("h4 visor colgado (suspendido)", h4_visor_colgado)
            run("h5 detener ordenado", h5_detener)
        run("j PID padre: el puente se cierra solo", j_pid_padre)
    finally:
        try:
            if SESION.vivo():
                registrar_pid(SESION)
                SESION.terminar()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def i_procesos():
        vivos = [p for p in MIS_PIDS if P.proceso_vivo(p)]
        for p in vivos:
            terminar_pid_propio(p)
        check(not vivos, f"quedaban visores propios vivos: {vivos}")
        perdidos = [p for p in ajenos if not P.proceso_vivo(p)]
        check(not perdidos, f"FreeCAD ajenos que ya no están: {perdidos} (¿los cerró el usuario?)")
        check(not (ajenos & MIS_PIDS), "un PID ajeno figura como propio")
        return f"propios lanzados {sorted(MIS_PIDS)} todos cerrados; ajenos vivos {sorted(ajenos)}"
    run("i procesos: sin visores propios vivos y FreeCAD del usuario intacto", i_procesos)

    print("\n== Tiempos ==")
    print(f"{'operación':55s} {'pared s':>8s}  {'read_count':>10s}  tiempos del puente")
    for op, dt, tp, rc in TIEMPOS:
        print(f"{op:55s} {dt:8.2f}  {'' if rc is None else rc:>10}  {tp or ''}")
    print("\n== Resumen ==")
    cuenta = {}
    for nombre, estado, detalle in RESULTADOS:
        cuenta[estado] = cuenta.get(estado, 0) + 1
        print(f"{estado:5s} {nombre}" + (f"\n      {detalle}" if estado == "XFAIL" else ""))
    print(" ".join(f"{k}={v}" for k, v in sorted(cuenta.items())))
    print("Capturas:", *COMPARTIDO.get("capturas", []), sep="\n  ")
    print("Exportación:", COMPARTIDO.get("exportacion"))
    return 1 if cuenta.get("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())

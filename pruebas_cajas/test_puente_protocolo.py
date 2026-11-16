"""Pruebas del protocolo del puente y del cliente SesionFreeCAD SIN FreeCAD.

Ejecutar (Python normal, desde la carpeta de la aplicación):
    python -m unittest discover -s pruebas_cajas -p "test_*.py"

El cliente se prueba contra un «puente falso» que corre en un hilo, lee las
peticiones del directorio de sesión y responde con el protocolo real
(cajas_protocolo). El proceso del visor se simula con un Python que duerme; las
pruebas solo terminan procesos lanzados por ellas mismas.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cajas_protocolo as P  # noqa: E402
import cajas_sesion_freecad as S  # noqa: E402

TOKEN = "t" * 64
CFG = {"step_path": r"C:\placa.stp", "wall": 3}


def pet(accion="estado", datos=None, **extra):
    p = P.peticion(TOKEN, accion, datos)
    p.update(extra)
    return p


def comprobar_estable(s, intentos=5):
    """comprobar() reintentando «iniciando»: en Windows, leer latido.json justo
    mientras el otro lado hace os.replace da PermissionError y leer_latido()
    devuelve None de forma transitoria (ver TestJson.test_reemplazo_con_lector)."""
    for _ in range(intentos):
        e = s.comprobar()
        if e != "iniciando":
            return e
        time.sleep(0.01)
    return e


def lanzar_durmiente(segundos=60):
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({segundos})"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# validar_peticion
# ---------------------------------------------------------------------------

class TestValidarPeticion(unittest.TestCase):
    def tipo(self, p, ahora=None):
        with self.assertRaises(P.ErrorProtocolo) as cm:
            P.validar_peticion(p, TOKEN, ahora)
        return cm.exception.tipo

    def test_acciones_validas(self):
        casos = {
            "estado": {},
            "cargar_placa": {"config": CFG},
            "calcular": {"config": CFG, "vista": "cerrada"},
            "vista": {"vista": "explosionada", "visible": {"base": False},
                      "transparencia": {"lid": 50}, "encuadrar": True},
            "exportar": {"config": CFG, "carpeta": r"C:\salida"},
            "captura": {"archivo": "vista_1.png", "ancho": 800, "alto": 600},
            "detectar": {"config": CFG, "margen": 0.5, "hasta_union": True},
            "componentes": {"config": CFG},
            "detener": {},
        }
        self.assertEqual(set(casos), set(P.ACCIONES))
        for accion, datos in casos.items():
            with self.subTest(accion=accion):
                p = pet(accion, datos)
                id_, acc, limpio = P.validar_peticion(p, TOKEN)
                self.assertEqual((id_, acc), (p["id"], accion))
        _, _, limpio = P.validar_peticion(pet("captura"), TOKEN)
        self.assertEqual(limpio, {"archivo": "captura.png", "ancho": 1200, "alto": 900})
        _, _, limpio = P.validar_peticion(pet("vista", {"vista": "separada", "transparencia": {"base": 33.4}}),
                                          TOKEN)
        self.assertEqual(limpio, {"vista": "separada", "transparencia": {"base": 33}})

    def test_token_incorrecto(self):
        self.assertEqual(self.tipo(dict(pet(), token="x" * 64)), "token")
        self.assertEqual(self.tipo(dict(pet(), token=None)), "token")
        self.assertEqual(self.tipo(dict(pet(), token=TOKEN[:-1])), "token")

    def test_id_invalido(self):
        for id_ in ("../x", "", "a" * 65, "a.b", "a b", None, 5, "..\\x"):
            with self.subTest(id_=id_):
                self.assertEqual(self.tipo(dict(pet(), id=id_)), "protocolo")
        P.validar_peticion(dict(pet(), id="a" * 64), TOKEN)  # 64 sí es válido

    def test_protocolo_distinto(self):
        for v in (2, 0, "1", None):
            with self.subTest(v=v):
                self.assertEqual(self.tipo(dict(pet(), protocolo=v)), "protocolo")

    def test_colores_validacion(self):
        _, _, datos = P.validar_peticion(pet("vista", {"colores": {"base": "#A0b1C2", "step": False}}), TOKEN)
        self.assertEqual(datos["colores"], {"base": "#A0b1C2", "step": False})
        for malo in ({"base": "rojo"}, {"base": "#12345"}, {"tapa": "#123456"}, {"step": "sí"}, "rojo"):
            with self.subTest(colores=malo), self.assertRaises(P.ErrorProtocolo) as ctx:
                P.validar_peticion(pet("vista", {"colores": malo}), TOKEN)
            self.assertEqual(ctx.exception.tipo, "validacion")

    def test_detectar_validacion(self):
        _, _, datos = P.validar_peticion(pet("detectar", {"config": CFG}), TOKEN)
        self.assertEqual((datos["margen"], datos["hasta_union"]), (0.5, False))
        for malo in ({"config": CFG, "margen": 6}, {"config": CFG, "margen": True},
                     {"config": CFG, "hasta_union": "sí"}, {"margen": 1}):
            with self.subTest(datos=malo), self.assertRaises(P.ErrorProtocolo) as ctx:
                P.validar_peticion(pet("detectar", malo), TOKEN)
            self.assertEqual(ctx.exception.tipo, "validacion")

    def test_accion_desconocida(self):
        for acc in ("borrar", "__import__('os')", "eval", "", None, ["estado"], "Estado"):
            with self.subTest(acc=acc):
                p = dict(pet(), accion=acc)
                with self.assertRaises(P.ErrorProtocolo) as cm:
                    P.validar_peticion(p, TOKEN)
                self.assertEqual(cm.exception.tipo, "protocolo")
                self.assertIn("no permitida", str(cm.exception))

    def test_datos_no_dict(self):
        for d in ([], "x", 3, None):
            with self.subTest(d=d):
                p = pet()
                p["datos"] = d   # datos: null tampoco es un objeto
                self.assertEqual(self.tipo(p), "protocolo")
        self.assertEqual(self.tipo(pet("calcular", {"config": "no"})), "validacion")
        self.assertEqual(self.tipo(pet("calcular", {})), "validacion")
        self.assertEqual(self.tipo(pet("cargar_placa", {"config": {"step_path": "  "}})), "validacion")

    def test_caducada(self):
        p = P.peticion(TOKEN, "estado", plazo=5)
        P.validar_peticion(p, TOKEN, ahora=p["creada"] + 4)
        self.assertEqual(self.tipo(p, ahora=p["creada"] + 6), "caducada")
        self.assertEqual(self.tipo(P.peticion(TOKEN, "estado", plazo=-1)), "caducada")
        self.assertEqual(self.tipo(dict(pet(), caduca="mañana")), "protocolo")
        self.assertEqual(self.tipo(dict(pet(), caduca=True)), "protocolo")

    def test_claves_extra_descartadas(self):
        p = pet("calcular", {"config": CFG, "malicioso": "__import__('os')", "vista": "cerrada"},
                extra_superior=1)
        _, _, limpio = P.validar_peticion(p, TOKEN)
        self.assertEqual(set(limpio), {"config", "vista"})
        _, _, limpio = P.validar_peticion(pet("estado", {"x": 1}), TOKEN)
        self.assertEqual(limpio, {})
        _, _, limpio = P.validar_peticion(pet("exportar", {"config": CFG, "carpeta": r"C:\a", "z": 1}), TOKEN)
        self.assertEqual(set(limpio), {"config", "carpeta"})

    def test_vista_visible_transparencia(self):
        malos = [
            {"vista": "lateral"}, {"vista": None},
            {"visible": {"tapa": True}}, {"visible": ["base"]}, {"visible": {"base": 1}},
            {"visible": {"base": "no"}},
            {"transparencia": {"lid": 101}}, {"transparencia": {"lid": -1}},
            {"transparencia": {"board": 50}}, {"transparencia": {"lid": "50"}},
            {"transparencia": 50}, {"encuadrar": 1},
        ]
        for d in malos:
            for acc in ("vista", "calcular"):
                with self.subTest(d=d, acc=acc):
                    datos = dict(d, config=CFG) if acc == "calcular" else d
                    self.assertEqual(self.tipo(pet(acc, datos)), "validacion")
        P.validar_peticion(pet("vista", {"transparencia": {"lid": 0, "base": 100}}), TOKEN)

    def test_bool_en_vez_de_numero(self):
        self.assertEqual(self.tipo(pet("vista", {"transparencia": {"lid": True}})), "validacion")
        self.assertEqual(self.tipo(pet("captura", {"ancho": True})), "validacion")
        self.assertEqual(self.tipo(pet("captura", {"alto": False})), "validacion")

    def test_carpeta_relativa(self):
        for c in ("salida", r".\salida", r"..\salida", "", None, 3):
            with self.subTest(c=c):
                self.assertEqual(self.tipo(pet("exportar", {"config": CFG, "carpeta": c})), "validacion")

    def test_captura_con_ruta(self):
        for a in (r"..\a.png", r"c:\x.png", "../a.png", "a/b.png", "a.jpg", "a.png.exe", ".png",
                  "x" * 41 + ".png", "ñandú.png", "a b.png", 5):
            with self.subTest(a=a):
                self.assertEqual(self.tipo(pet("captura", {"archivo": a})), "validacion")

    def test_ancho_alto_fuera_de_rango(self):
        for k in ("ancho", "alto"):
            for x in (199, 4001, 0, -5, 1200.5, "800", None):
                with self.subTest(k=k, x=x):
                    self.assertEqual(self.tipo(pet("captura", {k: x})), "validacion")
            for x in (200, 4000):
                P.validar_peticion(pet("captura", {k: x}), TOKEN)

    def test_no_objeto(self):
        for p in ([], "x", None, 3):
            with self.subTest(p=p):
                self.assertEqual(self.tipo(p), "protocolo")


# ---------------------------------------------------------------------------
# JSON atómico
# ---------------------------------------------------------------------------

class TestJson(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="test_puente_json_"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_ida_y_vuelta(self):
        ruta = self.dir / "datos ñandú.json"
        datos = {"texto": "Caja ñandú — pequeña «ó»", "n": [1, 2.5, None, True], "ruta": r"C:\Año\placa.stp"}
        P.escribir_json_atomico(ruta, datos)
        self.assertEqual(P.leer_json(ruta), datos)
        self.assertIn("ñandú", ruta.read_text(encoding="utf-8"))  # ensure_ascii=False
        P.escribir_json_atomico(ruta, {"otra": 1})               # reemplazo
        self.assertEqual(P.leer_json(ruta), {"otra": 1})
        self.assertEqual([p.name for p in self.dir.iterdir()], [ruta.name])  # sin .tmp sobrante

    @unittest.skipUnless(os.name == "nt", "bloqueo de archivos de Windows")
    def test_reemplazo_con_lector(self):
        """Regresión: si el destino está abierto por el otro lado, os.replace lanza
        PermissionError en Windows; escribir_json_atomico reintenta y nunca deja el
        temporal «.latido.json.<pid>.<x>.tmp» en el directorio de sesión."""
        ruta = self.dir / "latido.json"
        P.escribir_json_atomico(ruta, {"a": 1})
        with open(ruta, encoding="utf-8"):
            try:
                P.escribir_json_atomico(ruta, {"a": 2})
            except PermissionError:
                pass
        sobrantes = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(sobrantes, [])

    def test_tamano_maximo(self):
        ruta = self.dir / "grande.json"
        ruta.write_text(json.dumps({"x": "a" * (P.MAX_BYTES_PETICION + 10)}), encoding="utf-8")
        with self.assertRaises(P.ErrorProtocolo):
            P.leer_json(ruta)
        ruta.write_text(json.dumps({"x": "a" * (P.MAX_BYTES_PETICION - 100)}), encoding="utf-8")
        self.assertEqual(len(P.leer_json(ruta)["x"]), P.MAX_BYTES_PETICION - 100)

    def test_rutas(self):
        base = P.preparar_directorio(self.dir / "sesión")
        self.assertTrue((base / P.DIR_PETICIONES).is_dir() and (base / P.DIR_RESPUESTAS).is_dir())
        self.assertEqual(P.ruta_peticion(base, "ab").name, "ab.json")
        self.assertEqual(P.ruta_respuesta(base, "ab").parent.name, P.DIR_RESPUESTAS)
        r = P.respuesta(TOKEN, "ab", False, error="x", tipo="raro")
        self.assertEqual(r["tipo_error"], "interno")


# ---------------------------------------------------------------------------
# proceso_vivo
# ---------------------------------------------------------------------------

class TestProcesoVivo(unittest.TestCase):
    def test_propio(self):
        self.assertTrue(P.proceso_vivo(os.getpid()))

    def test_terminado(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=30)
        self.assertFalse(P.proceso_vivo(p.pid))

    def test_valores_no_validos(self):
        for v in (0, -1, -12345, None, "1234", 3.5, [1]):
            with self.subTest(v=v):
                self.assertFalse(P.proceso_vivo(v))

    def test_no_mata(self):
        p = lanzar_durmiente(30)
        try:
            for _ in range(20):
                self.assertTrue(P.proceso_vivo(p.pid))
            time.sleep(0.3)
            self.assertIsNone(p.poll(), "proceso_vivo terminó el proceso")
        finally:
            p.kill()
            p.wait(timeout=10)
        self.assertFalse(P.proceso_vivo(p.pid))


# ---------------------------------------------------------------------------
# SesionFreeCAD contra un puente falso
# ---------------------------------------------------------------------------

class PuenteFalso(threading.Thread):
    """Lee peticiones/ y responde con el protocolo real.

    `comportamiento[accion]` es una función (id, datos) -> dict de resultado o
    lanza P.ErrorProtocolo. Opciones: latido (bool), estado_latido, retraso,
    token_respuesta, sin_responder (conjunto de acciones), al_recibir (callback).
    """

    def __init__(self, base, token):
        super().__init__(daemon=True)
        self.base, self.token = Path(base), token
        self.alto = threading.Event()
        self.latido = True
        self.latido_ts_fijo = None
        self.estado_latido = "listo"
        self.retraso = 0.0
        self.token_respuesta = token
        self.sin_responder = set()
        self.al_recibir = None
        self.recibidas = []
        self.intervalo_latido = 0.2
        self._ultimo_latido = 0.0
        self._lat_lock = threading.Lock()
        self.comportamiento = {
            "estado": lambda id_, d: {"pid": os.getpid(), "falso": True},
            "calcular": lambda id_, d: {"config": d["config"]},
        }

    def escribir_latido(self):
        ts = self.latido_ts_fijo if self.latido_ts_fijo is not None else time.time()
        P.escribir_json_atomico(self.base / P.LATIDO, {"protocolo": P.PROTOCOLO, "token": self.token,
                                                       "pid": 0, "ts": ts, "estado": self.estado_latido})

    def pausar_latido(self):
        """Deja de escribir el latido; al volver, ninguna escritura está en curso."""
        with self._lat_lock:
            self.latido = False

    def responder(self, id_, ok, resultado=None, error=None, tipo=None):
        P.escribir_json_atomico(P.ruta_respuesta(self.base, id_),
                                P.respuesta(self.token_respuesta, id_, ok, resultado, error, tipo))

    def run(self):
        carpeta = self.base / P.DIR_PETICIONES
        while not self.alto.is_set():
            with self._lat_lock:
                if self.latido and time.time() - self._ultimo_latido >= self.intervalo_latido:
                    self._ultimo_latido = time.time()
                    try:
                        self.escribir_latido()
                    except OSError:   # carrera con un lector (Windows): se reintenta luego
                        self._ultimo_latido = 0.0
            try:
                nombres = sorted(n for n in os.listdir(carpeta) if n.endswith(".json") and not n.startswith("."))
            except OSError:
                nombres = []
            for n in nombres:
                ruta = carpeta / n
                try:
                    bruto = P.leer_json(ruta)
                    ruta.unlink()
                    id_, accion, datos = P.validar_peticion(bruto, self.token)
                except P.ErrorProtocolo as ex:
                    self.responder(n[:-5], False, error=str(ex), tipo=ex.tipo)
                    continue
                except (OSError, ValueError):
                    continue
                self.recibidas.append((id_, accion))
                if self.al_recibir:
                    self.al_recibir(id_, accion)
                if accion in self.sin_responder:
                    continue
                if self.retraso:
                    threading.Thread(target=self._tarde, args=(id_, accion, datos), daemon=True).start()
                else:
                    self._atender(id_, accion, datos)
            time.sleep(0.02)

    def _tarde(self, id_, accion, datos):
        time.sleep(self.retraso)
        self._atender(id_, accion, datos)

    def _atender(self, id_, accion, datos):
        fn = self.comportamiento.get(accion)
        try:
            if fn is None:
                raise P.ErrorProtocolo("sin manejador", "estado")
            self.responder(id_, True, resultado=fn(id_, datos))
        except P.ErrorProtocolo as ex:
            self.responder(id_, False, error=str(ex), tipo=ex.tipo)
        except OSError:   # la prueba ya terminó y borró el directorio
            pass

    def parar(self):
        self.alto.set()
        self.join(timeout=5)


class TestSesionConPuenteFalso(unittest.TestCase):
    def setUp(self):
        self._latido_original = S.LATIDO_VENCIDO
        self.s = S.SesionFreeCAD(Path(tempfile.gettempdir()) / "no_existe_freecad.exe")
        self.s.dir = P.preparar_directorio(Path(tempfile.mkdtemp(prefix="test_puente_sesion_")))
        self.s.token = P.nuevo_token()
        self.s.proc = lanzar_durmiente(120)
        self.procs = [self.s.proc]
        self.puente = PuenteFalso(self.s.dir, self.s.token)
        self.puente.start()

    def tearDown(self):
        S.LATIDO_VENCIDO = self._latido_original
        self.puente.parar()
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)
        if self.s.dir:
            shutil.rmtree(self.s.dir, ignore_errors=True)
        for p in self.procs:
            self.assertFalse(P.proceso_vivo(p.pid))

    def error(self, *a, **k):
        with self.assertRaises(S.ErrorSesion) as cm:
            self.s.solicitar(*a, **k)
        return cm.exception

    def en_hilo(self, *a, **k):
        caja = {}

        def f():
            try:
                caja["r"] = self.s.solicitar(*a, **k)
            except S.ErrorSesion as ex:
                caja["e"] = ex
        t = threading.Thread(target=f, daemon=True)
        t.start()
        t0 = time.time()
        while not self.s.ocupada and time.time() - t0 < 5:
            time.sleep(0.005)
        self.assertTrue(self.s.ocupada)
        return t, caja

    def test_ok_y_error(self):
        r = self.s.solicitar("estado", plazo=5)
        self.assertTrue(r["falso"])
        r = self.s.solicitar("calcular", {"config": {"wall": 3}}, plazo=5)
        self.assertEqual(r["config"], {"wall": 3})

        def falla(id_, d):
            raise P.ErrorProtocolo("Pared: muy delgada", "validacion")
        self.puente.comportamiento["calcular"] = falla
        ex = self.error("calcular", {"config": {"wall": 1}}, plazo=5)
        self.assertEqual(ex.tipo, "validacion")
        self.assertIn("Pared", str(ex))
        # Validación del lado del puente con el protocolo real.
        self.assertEqual(self.error("captura", {"archivo": "../x.png"}, plazo=5).tipo, "validacion")
        self.assertEqual(self.error("noexiste", plazo=5).tipo, "protocolo")
        self.assertFalse(self.s.ocupada)
        self.assertEqual(comprobar_estable(self.s), "conectada")

    def test_ocupado(self):
        self.puente.retraso = 1.0
        t, caja = self.en_hilo("estado", plazo=10)
        t1 = time.time()
        ex = self.error("estado", plazo=10)
        self.assertEqual(ex.tipo, "ocupado")
        self.assertLess(time.time() - t1, 0.2)
        self.assertEqual(comprobar_estable(self.s), "ocupada")
        t.join(10)
        self.assertIn("r", caja)
        self.assertEqual(len(self.puente.recibidas), 1)  # la segunda no llegó al puente

    def test_cancelar_y_respuesta_tardia(self):
        self.puente.sin_responder = {"calcular"}
        t, caja = self.en_hilo("calcular", {"config": {}}, plazo=60)
        while not self.puente.recibidas:
            time.sleep(0.01)
        id_viejo = self.puente.recibidas[0][0]
        t0 = time.time()
        self.s.cancelar_espera()
        t.join(5)
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(caja["e"].tipo, "cancelada")
        self.assertFalse(self.s.ocupada)
        # Llega tarde la respuesta de la petición cancelada.
        self.puente.responder(id_viejo, True, resultado={"viejo": True})
        viejo = P.ruta_respuesta(self.s.dir, id_viejo)
        self.assertTrue(viejo.is_file())
        r = self.s.solicitar("estado", plazo=5)
        self.assertTrue(r.get("falso"))
        self.assertNotIn("viejo", r)
        self.assertFalse(viejo.exists(), "la respuesta tardía no se descartó")
        # La cancelación no queda «pegada» para la siguiente petición.
        self.assertTrue(self.s.solicitar("estado", plazo=5)["falso"])

    def test_sin_latido(self):
        S.LATIDO_VENCIDO = 0.8
        self.puente.pausar_latido()
        (Path(self.s.dir) / P.LATIDO).unlink(missing_ok=True)
        self.puente.sin_responder = {"estado"}
        t0 = time.time()
        ex = self.error("estado", plazo=30)
        self.assertEqual(ex.tipo, "sin_respuesta")
        self.assertLess(time.time() - t0, 5)
        # Latido que existe pero quedó viejo con estado «listo»: también sin_respuesta.
        self.puente.latido_ts_fijo = time.time() - 100
        self.puente.escribir_latido()
        self.assertEqual(self.error("estado", plazo=30).tipo, "sin_respuesta")
        self.assertEqual(comprobar_estable(self.s), "sin_respuesta")

    def test_latido_ocupado_largo(self):
        S.LATIDO_VENCIDO = 0.5
        # Como en FreeCAD: el puente escribe «ocupado» una vez y el latido no avanza
        # mientras OCC calcula (no hay escritor concurrente).
        self.puente.pausar_latido()
        self.puente.estado_latido = "ocupado"
        self.puente.latido_ts_fijo = time.time() - 100
        self.puente.escribir_latido()
        self.puente.retraso = 2.5
        t0 = time.time()
        r = self.s.solicitar("estado", plazo=20)
        self.assertTrue(r["falso"])
        self.assertGreater(time.time() - t0, 2.0)
        self.assertEqual(comprobar_estable(self.s), "ocupada")
        # Pero el plazo sí se respeta.
        self.puente.sin_responder = {"estado"}
        t0 = time.time()
        self.assertEqual(self.error("estado", plazo=1.5).tipo, "tiempo")
        self.assertLess(time.time() - t0, 3)

    def test_fallo_transitorio_de_lectura_del_latido(self):
        """Regresión: tras LATIDO_VENCIDO, una sola lectura fallida del latido
        (p. ej. PermissionError mientras el puente hace os.replace) no debe convertir
        la espera a un puente «ocupado» en sin_respuesta: se usa el último latido válido."""
        S.LATIDO_VENCIDO = 0.3
        self.puente.pausar_latido()
        self.puente.estado_latido = "ocupado"
        self.puente.latido_ts_fijo = time.time() - 100
        self.puente.escribir_latido()
        self.puente.retraso = 1.5
        original = self.s.leer_latido
        estado = {"t0": time.time(), "fallo": False}

        def leer_con_fallo():
            if time.time() - estado["t0"] > 0.6 and not estado["fallo"]:
                estado["fallo"] = True
                return None          # una lectura transitoria fallida
            return original()
        self.s.leer_latido = leer_con_fallo
        self.assertTrue(self.s.solicitar("estado", plazo=10)["falso"])

    def test_proceso_muerto(self):
        self.puente.sin_responder = {"estado"}
        self.puente.al_recibir = lambda id_, acc: self.s.proc.kill()
        t0 = time.time()
        ex = self.error("estado", plazo=30)
        self.assertEqual(ex.tipo, "cerrada")
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(comprobar_estable(self.s), "cerrada")

    def test_plazo(self):
        self.puente.sin_responder = {"calcular"}
        t0 = time.time()
        ex = self.error("calcular", {"config": {}}, plazo=1.0)
        self.assertEqual(ex.tipo, "tiempo")
        self.assertLess(time.time() - t0, 3)

    def test_token_ajeno(self):
        self.puente.token_respuesta = "z" * 64
        ex = self.error("estado", plazo=5)
        self.assertEqual(ex.tipo, "protocolo")
        self.assertFalse(self.s.ocupada)

    def test_latido_ajeno_se_ignora(self):
        self.puente.pausar_latido()
        P.escribir_json_atomico(Path(self.s.dir) / P.LATIDO, {"token": "otro", "ts": time.time(),
                                                              "estado": "listo"})
        self.assertIsNone(self.s.leer_latido())
        self.assertEqual(comprobar_estable(self.s), "iniciando")

    def test_sin_sesion(self):
        dir_, token = self.s.dir, self.s.token
        self.s.dir = None
        self.assertEqual(self.error("estado").tipo, "sin_sesion")
        self.s.dir = dir_
        s2 = S.SesionFreeCAD("no_existe.exe")
        with self.assertRaises(S.ErrorSesion) as cm:
            s2.solicitar("estado")
        self.assertEqual(cm.exception.tipo, "sin_sesion")
        self.assertEqual(s2.comprobar(), "detenida")
        self.assertEqual(token, self.s.token)


if __name__ == "__main__":
    unittest.main()

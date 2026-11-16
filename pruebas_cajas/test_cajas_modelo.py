"""Pruebas de cajas_modelo (unittest, sin FreeCAD ni GUI).

Ejecutar desde la raíz del repositorio:
    python -m unittest discover -s pruebas_cajas -p "test_*.py"
o directamente:
    python pruebas_cajas/test_cajas_modelo.py
"""
import ast
import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import cajas_modelo as cm  # noqa: E402

FREECAD_PY = Path(r"C:\Program Files\FreeCAD 1.0\bin\python.exe")


def cfg(**kw):
    c = copy.deepcopy(cm.DEFAULTS)
    c.update(kw)
    return c


class Base(unittest.TestCase):
    def assertFalla(self, etiqueta, **kw):
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(**kw))
        self.assertIn(etiqueta, str(ctx.exception))
        return ctx.exception


class TestNormalizacion(Base):
    def test_defaults_validos(self):
        c = cm.config_from(cm.DEFAULTS)
        self.assertEqual(c["pcb_offset"], 0.0)
        self.assertEqual(set(c), set(cm.DEFAULTS))
        self.assertEqual(set(cm.LABELS), set(cm.DEFAULTS))
        self.assertEqual(cm.config_from({}), c)

    def test_comas_decimales(self):
        c = cm.config_from(cfg(wall="3,5", board_x=" 70,25 ", radius="2,5",
                               supports=[{"x": "1,5", "y": "-2,5", "height": "3,0",
                                          "diameter": "6,5", "hole": "2,5"}],
                               ports=[{"side": "frente", "u": "0,5", "v": "10,5",
                                       "width": "8,5", "height": "4,5"}]))
        self.assertEqual(c["wall"], 3.5)
        self.assertEqual(c["board_x"], 70.25)
        self.assertEqual(c["supports"][0], {"x": 1.5, "y": -2.5, "height": 3.0,
                                            "diameter": 6.5, "hole": 2.5})
        self.assertEqual(c["ports"][0]["v"], 10.5)

    def test_numero_invalido(self):
        e = self.assertFalla("Pared", wall="abc")
        self.assertEqual(e.field, "wall")
        self.assertFalla("Pared", wall="inf")
        self.assertFalla("Pared", wall=True)

    def test_booleanos(self):
        for v, esperado in (("true", True), ("1", True), (1, True), (True, True), ("Sí", True),
                            ("false", False), ("0", False), (0, False), (False, False), ("no", False)):
            self.assertIs(cm.config_from(cfg(export_stl=v))["export_stl"], esperado, v)
        self.assertFalla("Exportar STEP", export_step="quizás")

    def test_no_muta_entrada(self):
        entrada = cfg(wall="3,5", supports=[{"x": "1", "y": "2", "height": "3", "diameter": "6"}],
                      ports=[{"side": "Frente", "u": "0", "v": "10", "width": "8", "height": "4"}])
        original = copy.deepcopy(entrada)
        c = cm.config_from(entrada)
        self.assertEqual(entrada, original)
        c["supports"][0]["x"] = 99
        c["ports"].append({})
        self.assertEqual(entrada, original)
        d = cm.config_from(cm.DEFAULTS)
        d["supports"].append(1)
        self.assertEqual(cm.DEFAULTS["supports"], [])

    def test_claves_desconocidas_descartadas(self):
        c = cm.config_from(cfg(basura=1, supports=[{"x": 0, "y": 0, "height": 3,
                                                     "diameter": 6, "extra": 5}]))
        self.assertNotIn("basura", c)
        self.assertNotIn("extra", c["supports"][0])
        self.assertEqual(c["supports"][0]["hole"], 0.0)

    def test_claves_faltantes_items(self):
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(supports=[{"x": 0, "y": 0, "height": 3}]))
        self.assertIn("Soporte 1", str(ctx.exception))
        self.assertIn("Diámetro", str(ctx.exception))
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(ports=[{"side": "frente", "width": 5, "height": 3}]))
        self.assertIn("Posición v", str(ctx.exception))
        c = cm.config_from(cfg(ports=[{"side": "tapa", "width": 5, "height": 3}]))
        self.assertEqual((c["ports"][0]["u"], c["ports"][0]["v"]), (0.0, 0.0))

    def test_opciones(self):
        c = cm.config_from(cfg(up_axis="y", closure="Fricción", view="CERRADA"))
        self.assertEqual((c["up_axis"], c["closure"], c["view"]), ("Y", "friccion", "cerrada"))
        self.assertFalla("Eje vertical", up_axis="W")
        self.assertFalla("Cierre", closure="pegamento")
        self.assertFalla("Vista", view="otra")
        self.assertFalla("Cara", ports=[{"side": "arriba", "u": 0, "v": 5, "width": 2, "height": 2}])

    def test_rotacion(self):
        self.assertEqual(cm.config_from(cfg(rotation=-90))["rotation"], 270.0)
        self.assertEqual(cm.config_from(cfg(rotation="450,5"))["rotation"], 90.5)
        self.assertEqual(cm.config_from(cfg(rotation=360))["rotation"], 0.0)


class TestValidacion(Base):
    def test_nombre(self):
        self.assertFalla("Nombre", name="   ")

    def test_positivos_y_negativos(self):
        for k in ("board_x", "board_y", "board_z", "wall", "floor", "lid", "lid_depth"):
            self.assertFalla(cm.LABELS[k], **{k: 0})
        for k in ("margin", "bottom_gap", "top_gap", "clearance", "rib_interference", "radius", "pcb_offset"):
            self.assertFalla(cm.LABELS[k], **{k: -0.1})

    def test_pcb_offset(self):
        self.assertEqual(cm.config_from(cfg(pcb_offset="1,6"))["pcb_offset"], 1.6)
        self.assertFalla("Desfase del PCB", pcb_offset=12)
        c = cm.config_from(cfg(pcb_offset=1.6, bottom_gap=4))
        self.assertAlmostEqual(cm.suggested_support_height(c), 5.6)

    def test_reglas_cierre(self):
        self.assertFalla("Pared", wall=2.9)
        cm.config_from(cfg(wall=2.0, closure="apoyada"))  # válido sin ranuras
        self.assertFalla("Altura de lengüeta", tongue_height=0.9)
        self.assertFalla("Altura de lengüeta", tongue_height=4.1, lid_depth=5)
        self.assertFalla("Profundidad de tapa", tongue_height=2.6, lid_depth=3)
        self.assertFalla("Juego del encaje", clearance=0.51)
        self.assertFalla("Interferencia de nervios", rib_interference=0.09)
        self.assertFalla("Pared", wall=3.0, clearance=0.4)  # 1,2+0,8+1,2 = 3,2
        self.assertFalla("demasiado pequeña", board_x=5, board_y=5, radius=2)

    def test_tapa_y_radio(self):
        self.assertFalla("Profundidad de tapa", lid_depth=18, closure="apoyada")
        cm.config_from(cfg(radius=6.0))
        cm.config_from(cfg(radius=6.1))   # esquina interior 3,1 mm: no alcanza la placa
        self.assertFalla("Radio de esquina", radius=8, margin=0)   # pared de 0,93 mm en la esquina
        cm.config_from(cfg(radius=4, margin=0))

    def test_holgura_lateral_cero(self):
        c = cm.config_from(cfg(margin=0))
        self.assertEqual(cm.inner_corner_radius(c), 0.0)      # esquina interior viva
        d = cm.dimensions(c)
        self.assertEqual(d["inner_x"], c["board_x"])
        self.assertTrue(any("ajustada" in a for a in cm.warnings(c)))
        self.assertTrue(any("justa" in a for a in cm.warnings(cm.config_from(cfg(margin=0.1)))))
        self.assertEqual(cm.warnings(cm.config_from(cfg())), [])
        # Con holgura pequeña el redondeo interior se reduce para no entrar en la placa.
        c = cm.config_from(cfg(margin=0.2, radius=4, wall=3))
        self.assertLessEqual(cm.inner_corner_radius(c) * (1 - 2 ** -0.5), 0.2 + 1e-9)

    def test_medida_de_placa(self):
        self.assertEqual(cm.config_from(cfg())["board_fit"], "pcb")
        self.assertEqual(cm.config_from(cfg(board_fit="Personalizado"))["board_fit"], "personalizado")
        self.assertFalla("Medida de la placa", board_fit="otra")
        c = dict(cfg())
        del c["board_fit"]                    # proyectos antiguos: solo el PCB
        self.assertEqual(cm.config_from(c)["board_fit"], "pcb")

    def test_soportes(self):
        ok = {"x": 0, "y": 0, "height": 15, "diameter": 6, "hole": 2.5}
        cm.config_from(cfg(supports=[ok]))
        self.assertFalla("Soporte 1", supports=[{**ok, "height": 15.01}])
        self.assertFalla("plano de unión", supports=[{**ok, "height": 20}])
        self.assertFalla("fuera del espacio interior", supports=[{**ok, "x": 34, "height": 3}])
        cm.config_from(cfg(supports=[{**ok, "x": 33.5 - 3, "height": 3}]))
        self.assertFalla("Soporte 2", supports=[ok, {**ok, "y": -40}])
        self.assertFalla("agujero", supports=[{**ok, "hole": 6}])
        self.assertFalla("altura", supports=[{**ok, "height": 0}])
        self.assertFalla("diámetro", supports=[{**ok, "diameter": 0, "hole": 0}])

    def test_soporte_esquina_redondeada(self):
        # radio 6 y pared 3 → radio interior 3; la esquina interior (36.5) queda redondeada.
        s = {"x": 36.5 - 1.1, "y": 36.5 - 1.1, "height": 3, "diameter": 2}
        cm.config_from(cfg(supports=[s], radius=0))
        self.assertFalla("fuera del espacio interior", supports=[s], radius=6)

    def test_puertos_cada_cara(self):
        d = cm.dimensions(cm.config_from(cfg(board_x=80, board_y=60)))
        ox, oy, oz = d["outer_x"], d["outer_y"], d["outer_z"]  # 92, 72, 22.8
        for side, ancho in (("frente", ox), ("fondo", ox), ("izquierda", oy), ("derecha", oy)):
            borde = ancho/2 - 5
            cm.config_from(cfg(board_x=80, board_y=60, ports=[
                {"side": side, "u": borde, "v": 10, "width": 10, "height": 4}]))
            e = self.assertFalla("ancho de su cara", board_x=80, board_y=60, ports=[
                {"side": side, "u": borde + 0.1, "v": 10, "width": 10, "height": 4}])
            self.assertIn("Abertura 1", str(e))
            self.assertFalla("invade el piso", board_x=80, board_y=60, ports=[
                {"side": side, "u": 0, "v": 4, "width": 10, "height": 4}])
            self.assertFalla("supera la altura", board_x=80, board_y=60, ports=[
                {"side": side, "u": 0, "v": oz - 1, "width": 10, "height": 4}])
        # frente usa outer_x (92): u=40 con ancho 10 cabe; en izquierda (72) no.
        cm.config_from(cfg(board_x=80, board_y=60, ports=[
            {"side": "frente", "u": 40, "v": 10, "width": 10, "height": 4}]))
        self.assertFalla("ancho de su cara", board_x=80, board_y=60, ports=[
            {"side": "izquierda", "u": 40, "v": 10, "width": 10, "height": 4}])
        self.assertFalla("ancho y alto", ports=[{"side": "frente", "u": 0, "v": 10,
                                                  "width": 0, "height": 4}])

    def test_puerto_tapa(self):
        kw = dict(board_x=80, board_y=60)  # interior 86 x 66; no cortar la falda
        cm.config_from(cfg(ports=[{"side": "tapa", "u": 38, "v": 28, "width": 10, "height": 10}], **kw))
        self.assertFalla("interior de la tapa", ports=[
            {"side": "tapa", "u": 38.1, "v": 0, "width": 10, "height": 10}], **kw)
        self.assertFalla("interior de la tapa", ports=[
            {"side": "tapa", "u": 0, "v": 28.1, "width": 10, "height": 10}], **kw)
        self.assertFalla("interior de la tapa", ports=[
            {"side": "tapa", "u": 0, "v": 40, "width": 10, "height": 2}], **kw)


class TestDimensiones(unittest.TestCase):
    def test_dimensiones(self):
        d = cm.dimensions(cm.config_from(cfg(board_x=50, board_y=40, board_z=10, margin=2,
                                             wall=3, floor=2, lid=1.5, bottom_gap=4,
                                             top_gap=1, lid_depth=3)))
        self.assertEqual(d, {"inner_x": 54, "inner_y": 44, "inner_z": 15, "outer_x": 60,
                             "outer_y": 50, "outer_z": 18.5, "seam": 14, "ceiling": 17,
                             "lid_skirt": 3})

    def test_altura_de_union(self):
        base = dict(board_x=50, board_y=40, board_z=10, margin=2, wall=3, floor=2, lid=1.5,
                    bottom_gap=4, top_gap=1, lid_depth=3)
        d = cm.dimensions(cm.config_from(cfg(split_height=9, **base)))
        self.assertEqual((d["seam"], d["lid_skirt"]), (9, 8))
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(split_height=16, **base))       # falda < lengüeta + 0,5
        self.assertIn("Altura de la unión", str(ctx.exception))
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(split_height=2.5, **base))      # bajo piso + 1
        cm.config_from(cfg(split_height=16, closure="apoyada", **base))


class TestOpcionesNuevas(unittest.TestCase):
    def test_cierres_y_apoyos(self):
        for cierre in ("friccion", "labio", "imanes", "apoyada"):
            self.assertEqual(cm.config_from(cfg(closure=cierre))["closure"], cierre)
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(closure="pegamento"))
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(closure="imanes", magnet_height=11))
        self.assertIn("Alto del imán", str(ctx.exception))
        c = cm.config_from(cfg())
        self.assertEqual((c["board_rest"], c["hold_down"]), ("esquinas", False))
        self.assertTrue(cm.config_from(cfg(hold_down="sí"))["hold_down"])
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(board_rest="repisa", rest_width=0.2))

    def test_formas_de_abertura(self):
        p = {"side": "frente", "u": 0, "v": 8, "width": 6, "height": 3, "shape": "circular"}
        c = cm.config_from(cfg(ports=[p]))
        self.assertEqual(c["ports"][0]["height"], 6)          # circular: alto = diámetro
        self.assertEqual(c["ports"][0]["to_seam"], False)
        viejo = {"side": "frente", "u": 0, "v": 8, "width": 6, "height": 3}
        self.assertEqual(cm.config_from(cfg(ports=[viejo]))["ports"][0]["shape"], "rectangular")
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(ports=[dict(p, shape="estrella")]))
        q = dict(viejo, to_seam=True)
        self.assertEqual(cm.port_rect(cm.config_from(cfg(ports=[q]))["ports"][0], 14)[3], 14)
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(ports=[dict(viejo, v=19, to_seam=True)]))   # empieza sobre la unión (17,4)


class TestNombres(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(cm.safe_name("Caja ñandú"), "Caja ñandú")
        self.assertEqual(cm.safe_name('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")
        self.assertEqual(cm.safe_name("caja. . "), "caja")
        self.assertEqual(cm.safe_name("x\ty\n"), "x_y")
        self.assertEqual(cm.safe_name(""), "Caja")
        self.assertEqual(cm.safe_name("..."), "Caja")
        self.assertEqual(cm.safe_name("???"), "Caja")
        self.assertEqual(cm.safe_name("CON"), "_CON")
        self.assertEqual(cm.safe_name("nul.txt"), "_nul.txt")

    def test_unique_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ahora = dt.datetime(2026, 11, 15, 10, 20, 30)
            a = cm.unique_output_dir(tmp, "Caja: ñandú", now=ahora)
            b = cm.unique_output_dir(tmp, "Caja: ñandú", now=ahora)
            c = cm.unique_output_dir(tmp, "Caja: ñandú", now=ahora)
            self.assertEqual(a.name, "Caja_ ñandú_20261115_102030")
            self.assertEqual(b.name, a.name + "_2")
            self.assertEqual(c.name, a.name + "_3")
            self.assertTrue(a.is_dir() and b.is_dir() and c.is_dir())
            shutil.rmtree(b)  # aun así no reutiliza a ni c
            d = cm.unique_output_dir(tmp, "Caja: ñandú", now=ahora)
            self.assertNotIn(d, (a, c))
            e = cm.unique_output_dir(Path(tmp) / "nuevo padre", "x")
            self.assertTrue(e.is_dir())


class TestProyectos(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "Prueba cajas ñandú"
        self.tmp.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def test_ida_y_vuelta_step_relativo(self):
        step = self.tmp / "placas" / "mi placa ñ.step"
        step.parent.mkdir()
        step.write_text("x", encoding="utf-8")
        proyecto = self.tmp / "proyecto ñ.json"
        c = cm.config_from(cfg(step_path=str(step), wall="3,5", name="Caja ñandú",
                               supports=[{"x": 0, "y": 0, "height": 3, "diameter": 6}]))
        cm.save_project(proyecto, c)
        crudo = json.loads(proyecto.read_text(encoding="utf-8"))
        self.assertEqual(crudo["format"], "cajas-circuitos")
        self.assertEqual(crudo["version"], 1)
        self.assertEqual(crudo["config"]["step_path"], "placas/mi placa ñ.step")
        self.assertIn("ñandú", proyecto.read_text(encoding="utf-8"))
        cargado = cm.load_project(proyecto)
        self.assertEqual(Path(cargado["step_path"]), step.resolve())
        self.assertEqual({**cargado, "step_path": ""}, {**c, "step_path": ""})
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.suffix == ".tmp"], [])

    def test_step_absoluto(self):
        otra = Path(tempfile.mkdtemp())
        try:
            step = otra / "placa.step"
            proyecto = self.tmp / "p.json"
            cm.save_project(proyecto, cfg(step_path=str(step)))
            guardado = json.loads(proyecto.read_text(encoding="utf-8"))["config"]["step_path"]
            self.assertTrue(Path(guardado).is_absolute())
            self.assertEqual(Path(cm.load_project(proyecto)["step_path"]), step)
        finally:
            shutil.rmtree(otra, ignore_errors=True)

    def test_bom_y_sobrescritura(self):
        proyecto = self.tmp / "p.json"
        data = {"format": "cajas-circuitos", "version": 1, "config": {"wall": "4"}}
        proyecto.write_text(json.dumps(data), encoding="utf-8-sig")
        self.assertEqual(cm.load_project(proyecto)["wall"], 4.0)
        cm.save_project(proyecto, cfg(wall=5))
        self.assertEqual(cm.load_project(proyecto)["wall"], 5.0)

    def test_errores(self):
        p = self.tmp / "roto.json"
        p.write_text("{ no es json", encoding="utf-8")
        with self.assertRaisesRegex(cm.ConfigError, "dañado"):
            cm.load_project(p)
        p.write_text(json.dumps({"format": "otro", "version": 1, "config": {}}), encoding="utf-8")
        with self.assertRaisesRegex(cm.ConfigError, "no es un proyecto"):
            cm.load_project(p)
        p.write_text(json.dumps({"format": "cajas-circuitos", "version": 2, "config": {}}),
                     encoding="utf-8")
        with self.assertRaisesRegex(cm.ConfigError, "Versión"):
            cm.load_project(p)
        p.write_text(json.dumps({"format": "cajas-circuitos", "version": 1,
                                 "config": {"wall": 1}}), encoding="utf-8")
        with self.assertRaisesRegex(cm.ConfigError, "Pared"):
            cm.load_project(p)
        self.assertEqual(cm.load_project(p, validate_config=False)["wall"], 1.0)
        with self.assertRaises(cm.ConfigError):
            cm.save_project(self.tmp / "inv.json", cfg(wall=1))
        self.assertFalse((self.tmp / "inv.json").exists())


class TestMacro(unittest.TestCase):
    RUTA_RARA = r"C:\Mis placas\carpeta ñandú\it's \"raro\"\n\placa {x}.step"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "Prueba cajas ñandú"
        self.tmp.mkdir()
        self.config = cm.config_from(cfg(
            name='Caja "ñandú" \\ prueba', step_path=self.RUTA_RARA, pcb_offset="1,6",
            ports=[{"side": "tapa", "u": 0, "v": 0, "width": 5, "height": 5}],
            supports=[{"x": 0, "y": 0, "height": 3, "diameter": 6, "hole": 2}]))

    def tearDown(self):
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def _config_de_macro(self, texto):
        arbol = ast.parse(texto)
        for nodo in arbol.body:
            if isinstance(nodo, ast.Assign) and getattr(nodo.targets[0], "id", None) == "CONFIG":
                return ast.literal_eval(nodo.value)
        self.fail("La macro no define CONFIG")

    def test_macro(self):
        destino = self.tmp / "Caja ñandú.FCMacro"
        self.assertEqual(cm.write_macro(destino, self.config), destino)
        texto = destino.read_text(encoding="utf-8")
        compile(texto, str(destino), "exec")
        self.assertEqual(self._config_de_macro(texto), self.config)
        self.assertEqual(self._config_de_macro(texto)["step_path"], self.RUTA_RARA)
        nucleo = cm.macro_core()
        self.assertIn(nucleo, texto)
        self.assertIn("def create_document", texto)
        self.assertIn("create_document(CONFIG)", texto)
        self.assertNotIn("if __name__", texto)
        self.assertNotIn(cm.WORKER_MARKER, texto)
        self.assertNotIn("saveAs", texto.split("# CONFIGURACIÓN")[1])
        self.assertIn("Macro > Macros", texto)
        self.assertIn("BREP", texto)

    def test_no_sobrescribe(self):
        destino = self.tmp / "m.FCMacro"
        destino.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError) as ctx:
            cm.write_macro(destino, self.config)
        self.assertIn("Ya existe", str(ctx.exception))
        self.assertEqual(destino.read_text(encoding="utf-8"), "original")
        cm.write_macro(destino, self.config, overwrite=True)
        self.assertIn("CONFIG =", destino.read_text(encoding="utf-8"))

    def test_sin_marcador(self):
        falso = self.tmp / "nucleo.py"
        falso.write_text("x = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(cm.ConfigError, "WORKER_ENTRY"):
            cm.write_macro(self.tmp / "m.FCMacro", self.config, core_path=falso)
        self.assertFalse((self.tmp / "m.FCMacro").exists())

    def test_config_invalida_no_escribe(self):
        with self.assertRaises(cm.ConfigError):
            cm.write_macro(self.tmp / "m.FCMacro", cfg(wall=1))
        self.assertFalse((self.tmp / "m.FCMacro").exists())

    @unittest.skipUnless(FREECAD_PY.is_file(), "No está el Python de FreeCAD 1.0")
    def test_compila_con_python_freecad(self):
        destino = self.tmp / "Caja ñandú.FCMacro"
        cm.write_macro(destino, self.config)
        r = subprocess.run([str(FREECAD_PY), "-c",
                            "import sys; compile(open(sys.argv[1],encoding='utf-8').read(),"
                            " sys.argv[1], 'exec'); print('OK')", str(destino)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()


class TestAberturasTapa(unittest.TestCase):
    def setUp(self):
        self.d = cm.dimensions(cm.config_from(cfg()))          # interior ±36,5 mm

    def test_planta_juego_y_forma(self):
        ports, notas = cm.lid_ports([{"name": "Conn", "bbox": [-10, -5, 5, -2, 5, 12], "round": False},
                                     {"name": "Trim", "bbox": [20, 20, 5, 26, 26, 12], "round": True}],
                                    self.d, 0.5)
        self.assertEqual(notas, [])
        conn, trim = ports
        self.assertEqual((conn["u"], conn["v"], conn["width"], conn["height"], conn["shape"]),
                         (-6.0, 0.0, 9.0, 11.0, "redondeada"))
        self.assertLessEqual(conn["radius"], 0.5)               # radio ≤ juego: cubre las esquinas
        self.assertEqual((trim["shape"], trim["width"], trim["height"]), ("circular", 7.0, 7.0))
        cm.config_from(cfg(ports=ports))                       # válidas

    def test_union_borde_y_existentes(self):
        comps = [{"name": "A", "bbox": [-10, -5, 5, -2, 5, 12], "round": False},
                 {"name": "B", "bbox": [-1.8, -5, 5, 4, 5, 12], "round": False},   # a 0,2 mm de A
                 {"name": "Borde", "bbox": [30, -3, 5, 36.6, 3, 12], "round": False}]
        ports, _ = cm.lid_ports(comps, self.d, 0.5)
        self.assertEqual(len(ports), 2)                        # A y B unidas
        borde = ports[1]
        self.assertAlmostEqual(borde["u"] + borde["width"]/2, 36.5)   # llevada hasta la pared
        cm.config_from(cfg(ports=ports))
        otra, notas = cm.lid_ports(comps, self.d, 0.5, existing=cm.config_from(cfg(ports=ports))["ports"])
        self.assertEqual(otra, [])
        self.assertEqual(len(notas), 3)                        # todas ya tienen abertura

    def test_fuera_de_la_tapa(self):
        ports, notas = cm.lid_ports([{"name": "Lejos", "bbox": [50, 50, 5, 55, 55, 9], "round": False}],
                                    self.d, 0.5)
        self.assertEqual(ports, [])
        self.assertIn("fuera", notas[0])


class TestTornillos(unittest.TestCase):
    def test_agujeros_tipicos_y_propios(self):
        c = cm.config_from(cfg(closure="tornillos", screw_size="M4", screw_length=12))
        self.assertEqual(cm.screw_fit(c)["hole_d"], 4.0)          # sin inserto: diámetro nominal
        c = cm.config_from(cfg(closure="insertos", screw_size="M4", screw_length=12))
        self.assertEqual(cm.screw_fit(c)["hole_d"], 5.4)          # inserto M4
        c = cm.config_from(cfg(closure="tornillos", screw_size="m3", screw_hole="2,8"))
        self.assertEqual((c["screw_size"], cm.screw_fit(c)["hole_d"]), ("M3", 2.8))

    def test_largo_del_tornillo(self):
        c = cm.config_from(cfg(closure="tornillos", screw_size="M3", screw_length=10))
        f = cm.screw_fit(c)
        self.assertLess(f["min_len"], 10)
        self.assertGreater(f["max_len"], 10)
        with self.assertRaises(cm.ConfigError) as ctx:
            cm.config_from(cfg(closure="tornillos", screw_size="M3", screw_length=4))
        self.assertIn("Largo del tornillo", str(ctx.exception))
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(closure="insertos", screw_length=40))
        with self.assertRaises(cm.ConfigError):
            cm.config_from(cfg(closure="tornillos", screw_size="M5"))
        self.assertTrue(any("sirven de" in a for a in cm.warnings(c)))

    def test_tablas_iguales_en_geometria_y_validacion(self):
        import cajas_freecad as cf                                # importable sin FreeCAD
        self.assertEqual(cm.SCREWS, cf.SCREWS)
        self.assertEqual(cm.SCREW_CLOSURES, cf.SCREW_CLOSURES)
        self.assertEqual(cm.CLOSURES, cf.CLOSURES)

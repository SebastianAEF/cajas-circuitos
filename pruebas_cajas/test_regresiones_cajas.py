"""Regresiones de concurrencia, STEP y validación descubiertas en revisión."""
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cajas_modelo as M
import cajas_circuitos as G


class TestTrabajador(unittest.TestCase):
    def test_error_temporal_no_bloquea_la_interfaz(self):
        q = queue.Queue()
        t = G.Trabajador(q)
        t.ocupado = True
        with patch.object(G.tempfile, "mkdtemp", side_effect=OSError("sin espacio temporal")):
            t._correr(sys.executable, "inspect", {}, None, "prueba")
        self.assertFalse(t.ocupado)
        self.assertEqual(q.get_nowait()[0], "error")

    def test_no_admite_dos_trabajos(self):
        t = G.Trabajador(queue.Queue())
        t.ocupado = True
        with self.assertRaisesRegex(RuntimeError, "en curso"):
            t.lanzar(sys.executable, "preview", M.DEFAULTS)

    def test_publica_despues_de_liberar_estado(self):
        class Cola(queue.Queue):
            def put(self, value):
                self.estado_al_publicar = t.ocupado
                super().put(value)
        q = Cola()
        t = G.Trabajador(q)
        t.ocupado = t.cancelado = True
        with patch.object(G.subprocess, "Popen") as spawn:
            t._correr(sys.executable, "inspect", {}, None, "prueba")
        spawn.assert_not_called()
        self.assertFalse(q.estado_al_publicar)
        self.assertEqual(str(q.get_nowait()[2]), "Operación cancelada.")


class TestInterfaz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = G.crear_app()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app._ocupado(False)
        self.app._cargar_config(M.config_from({}))

    def test_bloquea_edicion_y_restaura_estados(self):
        a = self.app
        self.assertNotIn("disabled", a.e_step.state())
        a._ocupado(True, "prueba")
        self.assertIn("disabled", a.e_step.state())
        self.assertIn("disabled", a.cb_eje.state())
        self.assertNotIn("disabled", a.btn_cancelar.state())
        a._validar_en_vivo()
        self.assertTrue(all("disabled" in w.state() for w in a.e_board))
        with patch.object(a.trab, "lanzar") as launch, patch.object(a.tvisor, "lanzar") as visor:
            a.medir()
            a.vista_previa()
            a.generar()
            a.exportar_macro()
            a.abrir_visor()
        launch.assert_not_called()
        visor.assert_not_called()
        # «Reiniciar visor» queda disponible como recuperación durante un cálculo.
        self.assertNotIn("disabled", a.btn_visor_reiniciar.state())
        a._ocupado(False)
        self.assertNotIn("disabled", a.e_step.state())
        self.assertIn("readonly", a.cb_eje.state())
        self.assertIn("disabled", a.btn_cancelar.state())

    def test_step_modificado_invalida_medida(self):
        with tempfile.TemporaryDirectory() as folder:
            step = Path(folder)/"placa.step"
            step.write_text("original", encoding="utf-8")
            c = dict(step_path=str(step), up_axis="Z", rotation=0)
            key = self.app._clave_step(c)
            step.write_text("geometria actualizada", encoding="utf-8")
            self.assertNotEqual(key, self.app._clave_step(c))
            step.unlink()
            self.assertNotEqual(key, self.app._clave_step(c))

    def test_proyecto_no_confia_en_dimensiones_step_guardadas(self):
        c = M.config_from(dict(step_path="placa_que_ya_cambio.step"))
        self.app._cargar_config(c)
        self.assertTrue(self.app._step_pendiente(self.app._crudo()))

    def test_resultado_step_antiguo_no_cambia_proyecto(self):
        a = self.app
        a._clave_en_curso = ("otro.step", "Z", 0, None)
        a.cadena = [lambda: self.fail("no continuar con medida antigua")]
        a._fin_inspect({"size": [1, 2, 3]})
        self.assertIsNone(a.medida)
        self.assertFalse(a.cadena)
        self.assertIn("cambió", a.lbl_medida.cget("text"))

    def test_error_callback_no_detiene_poller(self):
        a = self.app
        a.cola.put(("ok", "v_calcular", {}, 0, "prueba"))
        with patch.object(a, "_fin_v_calcular", side_effect=ValueError("respuesta dañada")), \
             patch.object(G.messagebox, "showerror") as error, \
             patch.object(a, "after") as after:
            a._procesar_cola()
        error.assert_called_once()
        after.assert_called_once_with(80, a._procesar_cola)


class TestMacroMetadata(unittest.TestCase):
    def test_nombre_con_retorno_no_rompe_macro(self):
        text = M.macro_text(dict(name="Caja\rsegunda línea"))
        compile(text, "prueba.FCMacro", "exec")


if __name__ == "__main__":
    unittest.main()

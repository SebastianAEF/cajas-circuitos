"""Regresiones del generador; ejecutar con el Python incluido en FreeCAD."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cajas_modelo as M
import cajas_freecad as F


class TestGeometriaRevision(unittest.TestCase):
    def test_soporte_no_sobresale_por_piso_fino(self):
        cfg = M.config_from(dict(floor=0.1, supports=[
            dict(x=0, y=0, height=2, diameter=4, hole=0)]))
        result = F.build(cfg)
        self.assertAlmostEqual(result["base"].BoundBox.ZMin, 0, places=8)
        self.assertTrue(result["base"].isValid())
        self.assertEqual(len(result["base"].Solids), 1)

    def test_macro_rechaza_numeros_invalidos(self):
        for data in (dict(wall=float("nan")), dict(board_x=float("inf")), dict(top_gap=True)):
            with self.subTest(data=data), self.assertRaises(F.CajaError):
                F.build(dict(M.DEFAULTS, **data))


if __name__ == "__main__":
    unittest.main()

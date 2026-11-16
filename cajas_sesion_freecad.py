"""Cliente de la sesión persistente de FreeCAD (lado de la aplicación).

Arranca una instancia de FreeCAD administrada por la aplicación, que ejecuta
`cajas_puente_freecad.py`, y le envía peticiones por archivos JSON en un
directorio privado (ver cajas_protocolo.py). No usa tkinter: los métodos
bloqueantes se llaman desde un hilo de trabajo de la interfaz.

- La instancia usa una copia privada de user.cfg (--user-cfg), así que cerrar
  el visor no cambia las preferencias del FreeCAD del usuario.
- Nunca hay dos procesos de visor: `iniciar()` reutiliza la sesión si responde
  y, si no responde, la termina antes de lanzar otra.
- Un cálculo de OCC ocupa el hilo principal de FreeCAD y no se puede
  interrumpir desde fuera. «Cancelar» deja de esperar; la respuesta tardía se
  descarta. Para cortar un cálculo de verdad hay que terminar el visor
  (`terminar()`), lo que pierde la placa en memoria.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import cajas_protocolo as P

AQUI = Path(__file__).resolve().parent
PUENTE = AQUI / "cajas_puente_freecad.py"

LATIDO_VENCIDO = 15.0     # s sin latido estando «listo» = sin respuesta
ESPERA_ARRANQUE = 180.0   # s máximos hasta que el puente responde


class ErrorSesion(RuntimeError):
    """Fallo de la sesión. `tipo`: ocupado, sin_sesion, cerrada, sin_respuesta,
    tiempo, cancelada, arranque, o un tipo de error del puente (validacion...)."""

    def __init__(self, tipo, mensaje):
        super().__init__(mensaje)
        self.tipo = tipo


def freecad_desde(ruta):
    """freecad.exe a partir de su python.exe, freecadcmd.exe o carpeta bin."""
    if not ruta:
        return None
    p = Path(ruta)
    if p.is_dir():
        p = p / "freecad.exe"
    elif p.name.lower() != "freecad.exe":
        p = p.with_name("freecad.exe")
    return p if p.is_file() else None


class SesionFreeCAD:
    """Visor FreeCAD administrado: arranque, peticiones con espera y cierre, sin bloquear Tk."""
    def __init__(self, freecad_exe, puente=PUENTE, app_dir=AQUI, registro=None):
        self.freecad_exe = Path(freecad_exe)
        self.puente = Path(puente)
        self.app_dir = Path(app_dir)
        self.registro = registro or (lambda texto: None)
        self.proc = None
        self.dir = None
        self.token = None
        self.lanzamientos = 0              # procesos lanzados en total (para pruebas)
        self._peticion = threading.Lock()  # una petición a la vez
        self._ciclo = threading.RLock()    # arranque / parada
        self._cancelar = threading.Event()
        self.id_en_curso = None
        self._ultimo_latido = None

    # ------------------------------------------------------------ estado
    def vivo(self):
        """True si el proceso del visor está en marcha."""
        return self.proc is not None and self.proc.poll() is None

    @property
    def ocupada(self):
        """True mientras hay una petición en curso."""
        return self._peticion.locked()

    def leer_latido(self):
        """Último latido del puente de esta sesión. Si la lectura falla por un instante
        (el puente está reemplazando el archivo), devuelve el último latido válido."""
        if not self.dir:
            return None
        try:
            lat = P.leer_json(Path(self.dir) / P.LATIDO, intentos=5)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return self._ultimo_latido
        if lat.get("token") != self.token:
            return None
        self._ultimo_latido = lat
        return lat

    def comprobar(self):
        """Estado sin bloquear: detenida, cerrada, iniciando, conectada, ocupada o sin_respuesta."""
        if self.proc is None:
            return "detenida"
        if self.proc.poll() is not None:
            return "cerrada"
        lat = self.leer_latido()
        if lat is None:
            return "iniciando"
        if lat.get("estado") == "ocupado":
            return "ocupada"
        if lat.get("estado") == "deteniendo":
            return "cerrada"
        if time.time() - float(lat.get("ts", 0)) > LATIDO_VENCIDO:
            return "sin_respuesta"
        return "ocupada" if self.ocupada else "conectada"

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self, espera=ESPERA_ARRANQUE):
        """Garantiza una sesión que responde. Devuelve 'reutilizada' o 'nueva'."""
        with self._ciclo:
            if self.vivo():
                try:
                    self.solicitar("estado", plazo=10)
                    return "reutilizada"
                except ErrorSesion as ex:
                    if ex.tipo == "ocupado":
                        raise
                    self.registro("El visor no responde; se reinicia.")
                    self.terminar()
            elif self.proc is not None:
                self._limpiar()
            if not self.freecad_exe.is_file():
                raise ErrorSesion("arranque", f"No se encuentra FreeCAD: {self.freecad_exe}")
            if not self.puente.is_file():
                raise ErrorSesion("arranque", f"Falta {self.puente.name} junto a la aplicación.")
            self.dir = Path(tempfile.mkdtemp(prefix="cajas_sesion_"))
            P.preparar_directorio(self.dir)
            self.token = P.nuevo_token()
            cfg_usuario = Path(os.environ.get("APPDATA", "")) / "FreeCAD" / "user.cfg"
            cfg_privado = self.dir / "user.cfg"
            try:
                if cfg_usuario.is_file():
                    shutil.copyfile(cfg_usuario, cfg_privado)
            except OSError:
                pass
            env = dict(os.environ)
            for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
                env.pop(k, None)
            env.update({"CAJAS_PUENTE_DIR": str(self.dir), "CAJAS_PUENTE_TOKEN": self.token,
                        "CAJAS_PUENTE_APP": str(self.app_dir), "CAJAS_PUENTE_PADRE": str(os.getpid())})
            # Lista de argumentos, sin shell. freecad.exe es una aplicación gráfica.
            self.proc = subprocess.Popen(
                [str(self.freecad_exe), "--user-cfg", str(cfg_privado), str(self.puente)],
                cwd=str(self.dir), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.lanzamientos += 1
            self.registro(f"Visor FreeCAD lanzado (pid {self.proc.pid}).")
            t0 = time.time()
            while time.time() - t0 < espera:
                if self.proc.poll() is not None:
                    detalle = self._final_registro()
                    self._limpiar()
                    raise ErrorSesion("arranque", "FreeCAD se cerró al arrancar el visor." + detalle)
                lat = self.leer_latido()
                if lat and lat.get("pid") == self.proc.pid:
                    break
                if self._cancelar.is_set():
                    self._cancelar.clear()
                    self.terminar()
                    raise ErrorSesion("cancelada", "Arranque del visor cancelado.")
                time.sleep(0.2)
            else:
                detalle = self._final_registro()
                self.terminar()
                raise ErrorSesion("arranque", f"El puente de FreeCAD no respondió en {espera:.0f} s. "
                                  "¿Está FreeCAD configurado como instancia única?" + detalle)
            self.solicitar("estado", plazo=30)
            return "nueva"

    def detener(self, espera=15.0):
        """Cierre ordenado: pide al puente que cierre su documento y FreeCAD."""
        with self._ciclo:
            if not self.vivo():
                self._limpiar()
                return
            try:
                self.solicitar("detener", plazo=5)
            except ErrorSesion:
                pass
            try:
                self.proc.wait(timeout=espera)
            except subprocess.TimeoutExpired:
                self.registro("FreeCAD no se cerró a tiempo; se termina el proceso.")
                self.proc.kill()
                self.proc.wait(timeout=10)
            self._limpiar()

    def terminar(self):
        """Termina el proceso del visor sin esperar (recuperación)."""
        with self._ciclo:
            if self.vivo():
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._limpiar()

    def matar_sin_esperar(self):
        """Termina el proceso sin tomar ningún bloqueo (seguro desde el hilo de Tk aunque
        otro hilo esté esperando). La limpieza la hace el siguiente iniciar()."""
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _limpiar(self):
        """Borra el directorio de la sesión (con reintentos) y olvida el proceso."""
        self.proc = None
        if self.dir:
            # FreeCAD puede tardar un instante en soltar sus archivos al terminar.
            for _ in range(15):
                shutil.rmtree(self.dir, ignore_errors=True)
                if not Path(self.dir).exists():
                    break
                time.sleep(0.2)
        self.dir, self.token = None, None
        self._ultimo_latido = None

    def _final_registro(self):
        """Últimas líneas del registro del puente, para los mensajes de error."""
        try:
            texto = (Path(self.dir) / P.REGISTRO).read_text(encoding="utf-8")
            return "\n" + texto.strip()[-1200:] if texto.strip() else ""
        except (OSError, TypeError):
            return ""

    def cancelar_espera(self):
        """Deja de esperar la petición en curso (el cálculo de FreeCAD sigue)."""
        self._cancelar.set()

    # ------------------------------------------------------------ peticiones
    def solicitar(self, accion, datos=None, plazo=600.0):
        """Envía una petición y espera su respuesta. Devuelve `resultado` o lanza ErrorSesion."""
        if not self._peticion.acquire(blocking=False):
            raise ErrorSesion("ocupado", "Ya hay una operación en curso en el visor de FreeCAD.")
        try:
            self._cancelar.clear()
            if self.proc is not None and self.proc.poll() is not None:
                raise ErrorSesion("cerrada", "La ventana del visor FreeCAD se cerró. Usa «Reconectar».")
            if not self.vivo() or not self.dir:
                raise ErrorSesion("sin_sesion", "El visor de FreeCAD no está abierto.")
            pet = P.peticion(self.token, accion, datos, plazo=plazo)
            id_ = self.id_en_curso = pet["id"]
            # Respuestas tardías de peticiones abandonadas: se descartan.
            for viejo in (Path(self.dir) / P.DIR_RESPUESTAS).glob("*.json"):
                try:
                    viejo.unlink()
                except OSError:
                    pass
            P.escribir_json_atomico(P.ruta_peticion(self.dir, id_), pet)
            ruta = P.ruta_respuesta(self.dir, id_)
            t0 = time.time()
            while True:
                if ruta.is_file():
                    r = P.leer_json(ruta)
                    try:
                        ruta.unlink()
                    except OSError:
                        pass
                    if r.get("token") != self.token or r.get("id") != id_:
                        raise ErrorSesion("protocolo", "Respuesta ajena a esta sesión.")
                    if r.get("ok"):
                        return r.get("resultado", {})
                    raise ErrorSesion(r.get("tipo_error", "interno"), r.get("error", "Error en FreeCAD."))
                if self.proc is None or self.proc.poll() is not None:
                    raise ErrorSesion("cerrada", "FreeCAD se cerró durante la operación.")
                if self._cancelar.is_set():
                    raise ErrorSesion("cancelada", "Se dejó de esperar a FreeCAD. Si seguía calculando, "
                                      "el resultado se descartará.")
                lat = self.leer_latido() or self._ultimo_latido  # lectura fallida puntual
                ahora = time.time()
                # Ocupado (con esta u otra petición anterior abandonada): el latido no
                # avanza mientras OCC calcula; se espera hasta el plazo.
                calculando = lat and lat.get("estado") == "ocupado"
                if not calculando and ahora - t0 > LATIDO_VENCIDO:
                    if lat is None or ahora - float(lat.get("ts", 0)) > LATIDO_VENCIDO:
                        raise ErrorSesion("sin_respuesta", "El visor de FreeCAD no responde "
                                          "(sin latido). Reinícialo.")
                if ahora - t0 > plazo:
                    raise ErrorSesion("tiempo", f"FreeCAD no respondió en {plazo:.0f} s.")
                time.sleep(0.03)
        finally:
            self.id_en_curso = None
            self._peticion.release()

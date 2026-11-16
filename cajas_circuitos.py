"""Cajas para circuitos: interfaz gráfica.

Diseña una caja (base + tapa con cierre sin tornillos) alrededor de una placa
STEP o de una envolvente manual. La vista 3D es una instancia de FreeCAD
administrada por la aplicación (cajas_sesion_freecad.py + cajas_puente_freecad.py),
que conserva la placa en memoria y solo regenera base y tapa. Sin visor, las
mediciones y exportaciones usan el worker `cajas_freecad.py` en un proceso
aparte. Nunca se bloquea el hilo de Tk. Ejecutar con: python cajas_circuitos.py
"""
from __future__ import annotations

import glob
import copy
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter import font as tkfont

import cajas_idioma as I
import cajas_modelo as M
import cajas_sesion_freecad as S

AQUI = Path(__file__).resolve().parent
WORKER = AQUI / "cajas_freecad.py"
AJUSTES = AQUI / "cajas_ajustes_local.json"
TIEMPO_MAX = 900  # s por operación del worker

TEMAS = {
    "oscuro": {
        "bg": "#1b1d21", "panel": "#23262b", "field": "#2b2f35", "line": "#3a3f47",
        "text": "#eceff3", "muted": "#9aa1ab", "heading": "#8fb8ff",
        "primary": "#3b82f6", "primary_hi": "#5b96f7", "button": "#30343b",
        "button_hi": "#3c4149", "ok": "#4ade80", "warn": "#fbbf24", "error": "#f87171",
        "select": "#2563eb", "stripe": "#24272d",
    },
    "claro": {
        "bg": "#f4f5f7", "panel": "#e8eaee", "field": "#ffffff", "line": "#c9ced6",
        "text": "#1f2329", "muted": "#5f6773", "heading": "#1d4ed8",
        "primary": "#2563eb", "primary_hi": "#3b76f0", "button": "#e1e4e9",
        "button_hi": "#d3d7de", "ok": "#15803d", "warn": "#b45309", "error": "#c62828",
        "select": "#2563eb", "stripe": "#eef0f3",
    },
}
UI = dict(TEMAS["oscuro"])          # paleta activa (se reemplaza en sitio al cambiar el tema)
TAMANOS = (("pequeño", "Pequeño", -1), ("normal", "Normal", 0), ("grande", "Grande", 2))
# Colores por defecto de las piezas en el visor (los de cajas_freecad).
COLORES_FC = {"base": "#4aadd1", "lid": "#f2ab4d", "board": "#38a359", "step": True}


def T(texto):
    """Texto de la interfaz en el idioma elegido (ver cajas_idioma)."""
    return I.traducir(texto)
ESTADOS_VISOR = {
    "detenida": ("● Visor cerrado", "Muted.TLabel"),
    "iniciando": ("● Abriendo FreeCAD…", "Warn.TLabel"),
    "conectada": ("● Visor conectado", "Ok.TLabel"),
    "ocupada": ("● FreeCAD calculando…", "Warn.TLabel"),
    "sin_respuesta": ("● FreeCAD no responde", "Bad.TLabel"),
    "cerrada": ("● FreeCAD se cerró", "Bad.TLabel"),
}

LADOS = [("Frente (−Y)", "frente"), ("Fondo / trasera (+Y)", "fondo"),
         ("Izquierda (−X)", "izquierda"), ("Derecha (+X)", "derecha"),
         ("Tapa (techo)", "tapa")]
LADO_TXT = {k: t for t, k in LADOS}
LADO_KEY = {t: k for t, k in LADOS}
LADO_KEY.update({I.EN.get(t, t): k for t, k in LADOS})      # también en inglés
VISTAS = [("Separada (impresión)", "separada"), ("Cerrada", "cerrada"),
          ("Explosionada", "explosionada")]

CAMPOS_NUM = ["board_x", "board_y", "board_z", "pcb_offset", "rotation",
              "margin", "bottom_gap", "top_gap", "wall", "floor", "lid", "radius",
              "rest_width", "lid_depth", "split_height", "tongue_height", "clearance",
              "rib_interference", "magnet_diameter", "magnet_height", "magnet_clearance",
              "screw_length", "screw_hole", "insert_depth"]
FORMAS = [("Rectangular", "rectangular"), ("Redondeada", "redondeada"),
          ("Circular (Ø = ancho)", "circular"), ("Ovalada", "ovalada")]
FORMA_TXT = {k: t for t, k in FORMAS}
# Líneas de resultado que son información, no avisos.
INFO_AVISOS = ("Lengüetas:", "El ajuste", "Se agregaron", "Placa mostrada", "Imanes", "Medido")
AUTO_MS = 700   # espera tras el último cambio antes de actualizar el visor solo
FORMA_KEY = {t: k for t, k in FORMAS}
FORMA_KEY.update({I.EN.get(t, t): k for t, k in FORMAS})


def fmt(x):
    """Número compacto para mostrar en campos."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{x:.4f}".rstrip("0").rstrip(".") if abs(x) < 1e6 else f"{x:g}"


def etiqueta(clave):
    """Etiqueta legible de un campo de la configuración (de cajas_modelo.LABELS)."""
    return getattr(M, "LABELS", {}).get(clave, clave)


# --------------------------------------------------------------------------
# Localización del Python de FreeCAD
# --------------------------------------------------------------------------
def leer_ajustes():
    """Ajustes locales de la aplicación (ruta de FreeCAD, carpeta de salida, apariencia…)."""
    try:
        return json.loads(AJUSTES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_ajustes(datos):
    """Guarda los ajustes locales; un error de escritura no interrumpe la aplicación."""
    try:
        tmp = AJUSTES.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, AJUSTES)
    except OSError:
        pass


def python_desde(ruta):
    """Acepta python.exe, freecad.exe o freecadcmd.exe (o su carpeta bin)."""
    if not ruta:
        return None
    p = Path(ruta)
    if p.is_dir():
        p = p / "python.exe"
    elif p.name.lower() in ("freecad.exe", "freecadcmd.exe"):
        p = p.with_name("python.exe")
    return p if p.is_file() else None


def buscar_python_freecad(guardado=""):
    """Busca el python.exe de FreeCAD: el guardado en ajustes o las rutas típicas de Windows."""
    candidatos = [guardado, r"C:\Program Files\FreeCAD 1.0\bin\python.exe"]
    for patron in (r"C:\Program Files\FreeCAD*\bin\python.exe",
                   os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD*\bin\python.exe"),
                   os.path.expandvars(r"%LOCALAPPDATA%\FreeCAD*\bin\python.exe")):
        candidatos += sorted(glob.glob(patron), reverse=True)
    for c in candidatos:
        p = python_desde(c)
        if p:
            return p
    return None


# --------------------------------------------------------------------------
# Ejecución del worker de FreeCAD en un proceso aparte
# --------------------------------------------------------------------------
class Trabajador:
    """Ejecuta una orden del worker en un hilo; el resultado vuelve por cola."""

    def __init__(self, cola):
        self.cola = cola
        self.proc = None
        self.ocupado = False
        self.cancelado = False

    def lanzar(self, python, accion, config, folder=None, etiqueta_=""):
        """Arranca una acción del worker (inspect/preview/export) en segundo plano."""
        if self.ocupado:
            raise RuntimeError("Ya hay una operación de FreeCAD en curso.")
        self.ocupado, self.cancelado = True, False
        hilo = threading.Thread(target=self._correr, daemon=True,
                                args=(str(python), accion, copy.deepcopy(config), folder, etiqueta_))
        hilo.start()

    def cancelar(self):
        """Termina el proceso del worker en curso."""
        self.cancelado = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _correr(self, python, accion, config, folder, etiqueta_):
        """Hilo: escribe la petición, corre el worker, lee la respuesta y la deja en la cola."""
        t0 = time.time()
        tmp = None
        resultado = None
        try:
            tmp = tempfile.mkdtemp(prefix="cajas_worker_")
            if self.cancelado:
                raise RuntimeError("Operación cancelada.")
            req, resp = Path(tmp) / "request.json", Path(tmp) / "response.json"
            peticion = {"action": accion, "config": config}
            if folder:
                peticion["folder"] = str(folder)
            req.write_text(json.dumps(peticion, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
                env.pop(k, None)
            env["PYTHONIOENCODING"] = "utf-8"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # Lista de argumentos, sin shell: las rutas no se interpretan.
            self.proc = subprocess.Popen(
                [python, str(WORKER), str(req), str(resp)], cwd=tmp, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags)
            # Cancelar puede ocurrir antes de que Popen haya publicado el proceso.
            if self.cancelado:
                self.proc.kill()
            try:
                out, err = self.proc.communicate(timeout=TIEMPO_MAX)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate()
                raise RuntimeError(f"FreeCAD no terminó en {TIEMPO_MAX} s; se canceló la operación.")
            if self.cancelado:
                raise RuntimeError("Operación cancelada.")
            if not resp.is_file():
                detalle = (err or out or b"").decode("utf-8", "replace").strip()[-1500:]
                raise RuntimeError("El proceso de FreeCAD terminó sin respuesta "
                                   f"(código {self.proc.returncode}).\n{detalle}")
            datos = json.loads(resp.read_text(encoding="utf-8"))
            if not datos.get("ok"):
                ex = RuntimeError(datos.get("error") or "Error desconocido en FreeCAD.")
                ex.detalle = datos.get("traceback", "")
                raise ex
            resultado = ("ok", accion, datos["result"], time.time() - t0, etiqueta_)
        except Exception as ex:  # noqa: BLE001 - se informa en la GUI
            resultado = ("error", accion, ex, time.time() - t0, etiqueta_)
        finally:
            self.proc = None
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
            # El siguiente paso solo puede arrancar cuando terminó la limpieza.
            self.ocupado = False
            self.cola.put(resultado)


class TrabajadorVisor:
    """Ejecuta pasos con la sesión de FreeCAD en un hilo; el resultado vuelve por cola."""

    def __init__(self, cola):
        self.cola = cola
        self.ocupado = False
        self.sesion = None

    def lanzar(self, sesion, pasos, accion, etiqueta_=""):
        """Arranca `pasos(sesion)` en segundo plano (una operación a la vez)."""
        if self.ocupado:
            raise RuntimeError("Ya hay una operación del visor en curso.")
        self.ocupado, self.sesion = True, sesion
        threading.Thread(target=self._correr, args=(sesion, pasos, accion, etiqueta_),
                         daemon=True).start()

    def cancelar(self):
        """Deja de esperar la operación en curso (FreeCAD puede seguir calculando)."""
        if self.ocupado and self.sesion is not None:
            self.sesion.cancelar_espera()

    def _correr(self, sesion, pasos, accion, etiqueta_):
        """Hilo: ejecuta los pasos y publica ('ok' | 'error', acción, dato, duración, texto)."""
        t0 = time.time()
        try:
            resultado = ("ok", accion, pasos(sesion), time.time() - t0, etiqueta_)
        except Exception as ex:  # noqa: BLE001 - se informa en la GUI
            resultado = ("error", accion, ex, time.time() - t0, etiqueta_)
        finally:
            self.ocupado = False
        self.cola.put(resultado)


# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------
class DialogoComponentes:
    """Lista de componentes de la placa con casillas para crear aberturas en la tapa.

    Vienen marcados los que por su nombre parecen conectores o ajustes. Un clic en una
    fila (o la barra espaciadora) la marca o desmarca. `al_aceptar(elegidos, margen)`
    devuelve True si agregó aberturas; entonces la ventana se cierra.
    """

    def __init__(self, app, componentes, con_nombres, al_aceptar):
        self.app, self.al_aceptar = app, al_aceptar
        self.comps = sorted(componentes, key=lambda c: (not c["suggested"], c["name"].lower(),
                                                       c["bbox"][0], c["bbox"][1]))
        self.marcados = {c["id"] for c in self.comps if c["suggested"]}
        w = self.win = tk.Toplevel(app)
        w.title(T("Aberturas en la tapa"))
        w.configure(bg=UI["bg"])
        w.transient(app)
        w.geometry("720x520")
        w.minsize(560, 380)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Componentes a los que quieres llegar desde arriba", style="Heading.TLabel").pack(
            anchor="w")
        texto = ("Haz clic en una fila para marcarla. Vienen marcados los que parecen conectores, "
                 "trimmers o pulsadores por su nombre; no los que salen por una pared lateral "
                 "(esos llevan abertura en la pared).")
        if not con_nombres:
            texto = ("Este STEP se leyó sin nombres de componentes: la lista muestra cada sólido. "
                     "Haz clic en una fila para marcarla.")
        ttk.Label(f, text=texto, style="Help.TLabel", wraplength=680, justify="left").pack(anchor="w",
                                                                                        pady=(2, 8))
        fila = ttk.Frame(f)
        fila.pack(fill="x", pady=(0, 6))
        ttk.Label(fila, text="Buscar").pack(side="left")
        self.filtro = tk.StringVar()
        self.filtro.trace_add("write", lambda *_: self._llenar())
        ttk.Entry(fila, textvariable=self.filtro, width=24).pack(side="left", padx=8)
        ttk.Button(fila, text="Solo sugeridos", style="Small.TButton",
                   command=lambda: self._marcar("sugeridos")).pack(side="right")
        ttk.Button(fila, text="Ninguno", style="Small.TButton",
                   command=lambda: self._marcar("ninguno")).pack(side="right", padx=4)
        marco = ttk.Frame(f)
        marco.pack(fill="both", expand=True)
        cols = (("sel", "", 36), ("name", "Componente", 250), ("pos", "Centro X, Y", 120),
                ("size", "Planta", 110), ("top", "Altura sup.", 80), ("shape", "Abertura", 90))
        self.tv = ttk.Treeview(marco, columns=[c for c, _, _ in cols], show="headings", selectmode="none")
        for c, t, ancho in cols:
            self.tv.heading(c, text=t)
            self.tv.column(c, width=ancho, anchor="w" if c == "name" else "center", stretch=c == "name")
        self.tv.tag_configure("marcado", foreground=UI["ok"])
        self.tv.tag_configure("par", background=UI["stripe"])
        barra = ttk.Scrollbar(marco, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=barra.set)
        self.tv.pack(side="left", fill="both", expand=True)
        barra.pack(side="left", fill="y")
        self.tv.bind("<ButtonRelease-1>", self._clic)
        self.tv.bind("<space>", self._clic)
        pie = ttk.Frame(f)
        pie.pack(fill="x", pady=(10, 0))
        ttk.Label(pie, text="Juego alrededor").pack(side="left")
        self.margen = tk.StringVar(value=app.var_det_margen.get() or "0,5")
        ttk.Entry(pie, textvariable=self.margen, width=6, justify="right").pack(side="left", padx=(8, 4))
        ttk.Label(pie, text="mm", style="Help.TLabel").pack(side="left")
        self.lbl_cuenta = ttk.Label(pie, text="", style="Muted.TLabel")
        self.lbl_cuenta.pack(side="left", padx=16)
        ttk.Button(pie, text="Cancelar", command=w.destroy).pack(side="right")
        self.btn_ok = ttk.Button(pie, text="Crear aberturas", style="Primary.TButton", command=self._aceptar)
        self.btn_ok.pack(side="right", padx=8)
        self._llenar()
        app._traducir(w)
        w.grab_set()

    def _llenar(self):
        """Rellena la tabla según el filtro y marca los componentes elegidos."""
        texto = self.filtro.get().strip().lower()
        self.tv.delete(*self.tv.get_children())
        n = 0
        for c in self.comps:
            if texto and texto not in c["name"].lower():
                continue
            x0, y0, _z0, x1, y1, z1 = c["bbox"]
            marcado = c["id"] in self.marcados
            tags = (("marcado",) if marcado else ()) + (("par",) if n % 2 else ())
            self.tv.insert("", "end", iid=str(c["id"]), tags=tags, values=(
                "☑" if marcado else "☐", c["name"], f"{fmt(round((x0 + x1)/2, 1))}, {fmt(round((y0 + y1)/2, 1))}",
                f"{fmt(round(x1 - x0, 1))} × {fmt(round(y1 - y0, 1))}", fmt(round(z1, 1)),
                T("Sale por pared") if c.get("edge") else T("Circular" if c["round"] else "Rectangular")))
            n += 1
        self.lbl_cuenta.configure(text=f"{len(self.marcados)} marcados de {len(self.comps)}")

    def _clic(self, evento):
        """Marca o desmarca la fila pulsada (o la enfocada, con la barra espaciadora)."""
        iid = self.tv.identify_row(evento.y) if evento.type == tk.EventType.ButtonRelease else self.tv.focus()
        if not iid:
            return
        cid = int(iid)
        self.marcados.symmetric_difference_update({cid})
        self._llenar()
        self.tv.focus(iid)

    def _marcar(self, modo):
        """Marca solo los sugeridos o ninguno."""
        self.marcados = {c["id"] for c in self.comps if c["suggested"]} if modo == "sugeridos" else set()
        self._llenar()

    def _aceptar(self):
        """Valida el juego y entrega los componentes marcados a la aplicación."""
        try:
            margen = M.number(self.margen.get(), "Juego alrededor")
            if not 0 <= margen <= 5:
                raise ValueError("El juego alrededor debe estar entre 0 y 5 mm.")
        except ValueError as ex:
            messagebox.showerror("Aberturas en la tapa", str(ex), parent=self.win)
            return
        elegidos = [c for c in self.comps if c["id"] in self.marcados]
        if not elegidos:
            messagebox.showinfo("Aberturas en la tapa", "Marca al menos un componente.", parent=self.win)
            return
        if self.al_aceptar(elegidos, margen):
            self.win.destroy()


def crear_app():
    """Crea la ventana sin entrar al bucle (útil para pruebas)."""
    try:  # nitidez en pantallas con escalado
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    class App(tk.Tk):
        """Ventana principal: formularios a la izquierda, visor FreeCAD y avisos a la derecha."""
        def __init__(self):
            """Lee los ajustes, aplica idioma, tema y tamaño de texto y construye la interfaz."""
            super().__init__()
            self.title("Cajas para circuitos")
            self.geometry("1360x860")
            self.minsize(1160, 700)
            self.ajustes = leer_ajustes()
            I.fijar(self.ajustes.get("idioma", "es"))
            UI.update(TEMAS.get(self.ajustes.get("tema"), TEMAS["oscuro"]))
            self.fuentes = {
                "base": tkfont.Font(family="Segoe UI", size=10),
                "bold": tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                "small": tkfont.Font(family="Segoe UI", size=9),
                "small_bold": tkfont.Font(family="Segoe UI", size=9, weight="bold"),
                "title": tkfont.Font(family="Segoe UI", size=11, weight="bold"),
                "mono": tkfont.Font(family="Consolas", size=10),
                "mono_small": tkfont.Font(family="Consolas", size=8),
            }
            self._tamanos_base = {k: f.cget("size") for k, f in self.fuentes.items()}
            self._aplicar_tamano(self.ajustes.get("texto", "normal"))
            self._repintar = []           # funciones que redibujan textos al cambiar el idioma
            self.configure(bg=UI["bg"])
            self._estilo()

            self.python_fc = buscar_python_freecad(self.ajustes.get("freecad_python", ""))
            self.cola = queue.Queue()
            self.trab = Trabajador(self.cola)
            self.tvisor = TrabajadorVisor(self.cola)
            self.sesion = None            # SesionFreeCAD (visor), se crea al abrirlo
            self._tarea_vista = None
            self.proyecto = None          # ruta del proyecto JSON abierto
            self.modificado = False
            self.medida_clave = None      # (step, eje, giro) de la última medición
            self.medida = None            # resultado de inspect
            self.vista_clave = None       # configuración de la caja que muestra el visor
            self.cadena = []              # pasos pendientes tras el trabajo actual
            self.supports, self.ports = [], []
            self._cargando = False
            self._tarea_val = None
            self._llenando_form = False   # _sel_tabla rellena el formulario: no es una edición
            self._tarea_auto = None       # actualización automática del visor programada
            self.dialogo_componentes = None
            self._ui_bloqueada = False
            self._reiniciando = False
            self._estados_controles = []

            self.v = {}
            self._construir()
            self._traducir(self)
            self._cargar_config(M.config_from({}))
            self.modificado = False
            self._actualizar_titulo()
            self._actualizar_fc()
            self.protocol("WM_DELETE_WINDOW", self._cerrar)
            self.after(80, self._procesar_cola)
            self.after(1000, self._sondear_visor)

        # ------------------------------------------------------------ estilo
        def _estilo(self):
            """Configura los estilos ttk con la paleta y las fuentes activas (se puede repetir en vivo)."""
            st = ttk.Style(self)
            try:
                st.theme_use("clam")
            except tk.TclError:
                pass
            f = self.fuentes["base"]
            st.configure(".", background=UI["bg"], foreground=UI["text"], font=f,
                         fieldbackground=UI["field"], bordercolor=UI["line"],
                         lightcolor=UI["line"], darkcolor=UI["line"],
                         troughcolor=UI["panel"], insertcolor=UI["text"],
                         selectbackground=UI["select"], selectforeground="white")
            st.configure("TFrame", background=UI["bg"])
            st.configure("Panel.TFrame", background=UI["panel"])
            st.configure("TLabel", background=UI["bg"], foreground=UI["text"])
            st.configure("Muted.TLabel", foreground=UI["muted"])
            st.configure("Heading.TLabel", foreground=UI["heading"], font=self.fuentes["bold"])
            st.configure("Ok.TLabel", foreground=UI["ok"])
            st.configure("Warn.TLabel", foreground=UI["warn"])
            st.configure("Bad.TLabel", foreground=UI["error"])
            st.configure("TLabelframe", background=UI["bg"], bordercolor=UI["line"])
            st.configure("TLabelframe.Label", background=UI["bg"], foreground=UI["heading"],
                         font=self.fuentes["bold"])
            st.configure("TNotebook", background=UI["bg"], borderwidth=0)
            st.configure("TNotebook.Tab", background=UI["button"], foreground=UI["text"],
                         padding=(10, 5))
            st.map("TNotebook.Tab", background=[("selected", UI["primary"]),
                                                ("active", UI["button_hi"])])
            st.configure("TButton", background=UI["button"], foreground=UI["text"],
                         padding=(10, 4), borderwidth=1)
            st.map("TButton", background=[("disabled", UI["panel"]), ("active", UI["button_hi"])],
                   foreground=[("disabled", UI["muted"])])
            st.configure("Primary.TButton", background=UI["primary"], foreground="white")
            st.map("Primary.TButton", background=[("disabled", UI["panel"]),
                                                  ("active", UI["primary_hi"])])
            st.configure("TEntry", fieldbackground=UI["field"], foreground=UI["text"])
            st.map("TEntry", fieldbackground=[("readonly", UI["panel"]), ("disabled", UI["panel"])],
                   foreground=[("disabled", UI["muted"])])
            st.configure("TCombobox", fieldbackground=UI["field"], foreground=UI["text"],
                         background=UI["button"], arrowcolor=UI["text"])
            st.map("TCombobox", fieldbackground=[("readonly", UI["field"])],
                   foreground=[("readonly", UI["text"])])
            for w in ("TRadiobutton", "TCheckbutton"):
                st.configure(w, background=UI["bg"], foreground=UI["text"],
                             indicatorcolor=UI["field"])
                st.map(w, background=[("active", UI["bg"])],
                       indicatorcolor=[("selected", UI["primary"])],
                       foreground=[("disabled", UI["muted"])])
            st.configure("Treeview", background=UI["field"], fieldbackground=UI["field"],
                         foreground=UI["text"], rowheight=24)
            st.map("Treeview", background=[("selected", UI["select"])])
            st.configure("Treeview.Heading", background=UI["button"], foreground=UI["text"])
            st.configure("TPanedwindow", background=UI["line"])
            st.configure("Horizontal.TProgressbar", background=UI["primary"])
            self.option_add("*TCombobox*Listbox.background", UI["field"])
            self.option_add("*TCombobox*Listbox.foreground", UI["text"])
            self.option_add("*TCombobox*Listbox.selectBackground", UI["select"])
            self.option_add("*Menu.background", UI["panel"])
            self.option_add("*Menu.foreground", UI["text"])
            self.option_add("*Menu.activeBackground", UI["select"])
            st.configure("Help.TLabel", foreground=UI["muted"], font=self.fuentes["small"])
            st.configure("TLabelframe", padding=(12, 6, 12, 10))
            st.configure("TNotebook.Tab", padding=(14, 6))
            st.configure("TEntry", padding=(6, 3))
            st.configure("Toolbar.TButton", background=UI["bg"], borderwidth=0, padding=(10, 5))
            st.map("Toolbar.TButton", background=[("active", UI["button_hi"])])
            st.configure("Toolbar.TMenubutton", background=UI["bg"], borderwidth=0, padding=(10, 5),
                         arrowcolor=UI["text"])
            st.map("Toolbar.TMenubutton", background=[("active", UI["button_hi"])])
            st.configure("Small.TButton", padding=(8, 3))
            st.configure("Section.TButton", background=UI["panel"], foreground=UI["heading"],
                         font=self.fuentes["bold"], anchor="w", padding=(10, 6), borderwidth=0)
            st.map("Section.TButton", background=[("active", UI["button_hi"])])
            st.configure("Treeview.Heading", font=self.fuentes["small_bold"], padding=(4, 4))
            st.configure("Treeview", rowheight=int(self.fuentes["base"].metrics("linespace")*1.6))
            st.configure("TSeparator", background=UI["line"])

        # ------------------------------------------------------- construcción
        def _construir(self):
            """Arma la ventana: barra superior con el menú, pestañas, medidas, visor y barra de estado."""
            barra = ttk.Frame(self, padding=(10, 8, 10, 6))
            barra.pack(fill="x")
            self.btn_menu = ttk.Menubutton(barra, text="☰  " + T("Opciones"), style="Toolbar.TMenubutton")
            self.btn_menu.pack(side="left", padx=(0, 8))
            self.menu = tk.Menu(self.btn_menu, tearoff=False)
            self.btn_menu["menu"] = self.menu
            self.var_idioma = tk.StringVar(value=I.actual())
            self.var_tema = tk.StringVar(value=self.ajustes.get("tema", "oscuro"))
            self.var_texto = tk.StringVar(value=self.ajustes.get("texto", "normal"))
            for txt, cmd in (("Nuevo", self.nuevo), ("Abrir…", self.abrir),
                             ("Guardar", self.guardar), ("Guardar como…", self.guardar_como)):
                ttk.Button(barra, text=txt, style="Toolbar.TButton", command=cmd).pack(side="left", padx=(0, 2))
            ttk.Button(barra, text="Ayuda", style="Toolbar.TButton", command=self.ayuda).pack(side="right")
            ttk.Button(barra, text="FreeCAD…", style="Toolbar.TButton",
                       command=self.elegir_freecad).pack(side="right", padx=(0, 2))
            self.lbl_fc = ttk.Label(barra, text="", style="Help.TLabel")
            self.lbl_fc.pack(side="right", padx=10)
            ttk.Separator(self, orient="horizontal").pack(fill="x")

            panel = ttk.PanedWindow(self, orient="horizontal")
            panel.pack(fill="both", expand=True, padx=10, pady=(8, 4))
            izq = ttk.Frame(panel)
            der = ttk.Frame(panel, padding=(10, 0, 0, 0))
            panel.add(izq, weight=0)
            panel.add(der, weight=1)

            nb = ttk.Notebook(izq, width=580)
            nb.pack(fill="both", expand=True)
            self._tab_placa(nb)
            self._tab_caja(nb)
            self._tab_cierre(nb)
            self._tab_aberturas(nb)
            self._tab_soportes(nb)
            self._tab_exportar(nb)

            res = ttk.LabelFrame(izq, text="Medidas y validación", padding=(12, 6, 12, 8))
            res.pack(fill="x", pady=(8, 0))
            self.lbl_dims = ttk.Label(res, text="", justify="left", font=self.fuentes["mono"])
            self.lbl_dims.pack(anchor="w")
            self.lbl_val = ttk.Label(res, text="", wraplength=540, justify="left")
            self.lbl_val.pack(anchor="w", pady=(4, 0))

            self._panel_visor(der)
            self._construir_menu()

            ttk.Separator(self, orient="horizontal").pack(fill="x")
            pie = ttk.Frame(self, padding=(10, 4, 10, 6))
            pie.pack(fill="x")
            self.prog = ttk.Progressbar(pie, mode="indeterminate", length=140)   # visible solo al trabajar
            self.btn_cancelar = ttk.Button(pie, text="Cancelar", command=self.cancelar,
                                           state="disabled")
            self.btn_cancelar.pack(side="right", padx=8)
            self.lbl_estado = ttk.Label(pie, text=T("Listo."), style="Muted.TLabel")
            self.lbl_estado.pack(side="left", fill="x", expand=True)

        # ------------------------------------------------------- personalización
        def _construir_menu(self):
            """Menú «☰ Opciones»: idioma, tema, tamaño del texto, colores en FreeCAD y más."""
            m = self.menu
            for hijo in m.winfo_children():
                hijo.destroy()
            m.delete(0, "end")
            colores = dict(bg=UI["panel"], fg=UI["text"], activebackground=UI["select"],
                           activeforeground="white", selectcolor=UI["text"])
            m.configure(**colores)
            idioma = tk.Menu(m, tearoff=False, **colores)
            for cod, nombre in I.IDIOMAS:
                idioma.add_radiobutton(label=nombre, value=cod, variable=self.var_idioma,
                                       command=self._cambiar_idioma)
            tema = tk.Menu(m, tearoff=False, **colores)
            for cod, nombre in (("oscuro", "Oscuro"), ("claro", "Claro")):
                tema.add_radiobutton(label=T(nombre), value=cod, variable=self.var_tema,
                                     command=self._cambiar_tema)
            texto = tk.Menu(m, tearoff=False, **colores)
            for cod, nombre, _ in TAMANOS:
                texto.add_radiobutton(label=T(nombre), value=cod, variable=self.var_texto,
                                      command=self._cambiar_texto)
            m.add_cascade(label=T("Idioma"), menu=idioma)
            m.add_cascade(label=T("Tema"), menu=tema)
            m.add_cascade(label=T("Tamaño del texto"), menu=texto)
            m.add_separator()
            m.add_command(label=T("Colores en FreeCAD…"), command=self.dialogo_colores)
            m.add_checkbutton(label=T("Actualizar el visor automáticamente"), variable=self.var_auto)
            m.add_separator()
            m.add_command(label=T("Restablecer apariencia"), command=self._restablecer_apariencia)
            self.btn_menu.configure(text="☰  " + T("Opciones"))

        def _guardar_apariencia(self, **valores):
            """Guarda en los ajustes las opciones de apariencia indicadas."""
            self.ajustes.update(valores)
            guardar_ajustes(self.ajustes)

        def _cambiar_idioma(self):
            """Aplica el idioma elegido a toda la interfaz sin reiniciar."""
            I.fijar(self.var_idioma.get())
            self._guardar_apariencia(idioma=I.actual())
            self._traducir(self)
            for pintar in self._repintar:
                pintar()
            self._construir_menu()
            self._actualizar_titulo()
            self._actualizar_visor()
            self._validar_en_vivo()
            self.lbl_estado.configure(text=T("Listo."), style="Muted.TLabel")

        def _cambiar_tema(self):
            """Aplica el tema claro u oscuro a los estilos ttk y a los widgets tk."""
            tema = self.var_tema.get() if self.var_tema.get() in TEMAS else "oscuro"
            UI.clear()
            UI.update(TEMAS[tema])
            self._guardar_apariencia(tema=tema)
            self._estilo()
            self._repintar_tk(self)
            self._construir_menu()

        def _aplicar_tamano(self, clave):
            """Ajusta el tamaño de todas las fuentes con nombre según la opción elegida."""
            delta = {k: d for k, _, d in TAMANOS}.get(clave, 0)
            for k, f in self.fuentes.items():
                f.configure(size=self._tamanos_base[k] + delta)

        def _cambiar_texto(self):
            """Aplica y guarda el tamaño del texto."""
            self._aplicar_tamano(self.var_texto.get())
            self._guardar_apariencia(texto=self.var_texto.get())
            self._estilo()

        def _restablecer_apariencia(self):
            """Vuelve a español, tema oscuro, texto normal y colores por defecto."""
            self.var_idioma.set("es")
            self.var_tema.set("oscuro")
            self.var_texto.set("normal")
            self.ajustes.pop("colores_fc", None)
            self._cambiar_texto()
            self._cambiar_tema()
            self._cambiar_idioma()
            self._programar_vista()

        def _traducir(self, raiz):
            """Pasa al idioma activo los textos fijos de `raiz` y sus hijos. Solo cambia los
            textos que conoce (en cualquiera de los dos idiomas); los demás quedan igual."""
            pila = [raiz]
            while pila:
                w = pila.pop()
                pila.extend(w.winfo_children())
                try:
                    if isinstance(w, ttk.Notebook):
                        for t in w.tabs():
                            w.tab(t, text=T(w.tab(t, "text")))
                    elif isinstance(w, ttk.Treeview):
                        for col in w["columns"]:
                            w.heading(col, text=T(w.heading(col, "text")))
                    elif isinstance(w, ttk.Combobox):
                        valores = w.cget("values")
                        if valores:
                            w.configure(values=[T(v) for v in valores])
                        actual = w.get()
                        if actual and T(actual) != actual:
                            w.set(T(actual))
                    elif "text" in w.keys():
                        texto = w.cget("text")
                        if isinstance(texto, str) and texto and T(texto) != texto:
                            w.configure(text=T(texto))
                except tk.TclError:
                    pass

        def _repintar_tk(self, raiz):
            """Colores de los widgets tk clásicos (los ttk siguen al estilo)."""
            pila = [raiz]
            while pila:
                w = pila.pop()
                pila.extend(w.winfo_children())
                try:
                    if isinstance(w, (tk.Tk, tk.Toplevel)):
                        w.configure(bg=UI["bg"])
                    elif isinstance(w, tk.Canvas):
                        w.configure(bg=UI["bg"])
                    elif isinstance(w, tk.Text):
                        w.configure(bg=UI["panel"], fg=UI["text"], insertbackground=UI["text"])
                        for tag, color in (("aviso", "warn"), ("error", "error"), ("info", "muted"),
                                           ("detalle", "muted")):
                            w.tag_configure(tag, foreground=UI[color])
                    elif isinstance(w, ttk.Treeview):
                        w.tag_configure("par", background=UI["stripe"])
                        w.tag_configure("marcado", foreground=UI["ok"])
                    elif isinstance(w, tk.Label):
                        w.configure(highlightbackground=UI["line"])
                except tk.TclError:
                    pass

        def _colores_fc(self):
            """Colores del visor (base, tapa, placa y si usar los del STEP), con los por defecto."""
            c = dict(COLORES_FC)
            c.update(self.ajustes.get("colores_fc") or {})
            return {k: c[k] for k in ("base", "lid", "board", "step")}

        def dialogo_colores(self):
            """Colores de base, tapa y placa en el visor; se aplican al momento."""
            w = tk.Toplevel(self)
            w.title(T("Colores en FreeCAD"))
            w.configure(bg=UI["bg"])
            w.transient(self)
            w.resizable(False, False)
            f = ttk.Frame(w, padding=16)
            f.pack(fill="both", expand=True)
            colores = self._colores_fc()
            muestras = {}

            def guardar():
                self._guardar_apariencia(colores_fc=dict(colores))
                self._programar_vista()

            def pintar():
                for k, lbl in muestras.items():
                    lbl.configure(bg=colores[k])
            for i, (k, txt) in enumerate((("base", "Base"), ("lid", "Tapa"),
                                          ("board", "Placa (sin colores del STEP)"))):
                ttk.Label(f, text=T(txt)).grid(row=i, column=0, sticky="w", pady=4)
                m = tk.Label(f, width=6, bg=colores[k], relief="flat", highlightthickness=1,
                             highlightbackground=UI["line"])
                m.grid(row=i, column=1, padx=12, sticky="ns", pady=4)
                muestras[k] = m

                def cambiar(k=k, txt=txt):
                    elegido = colorchooser.askcolor(color=colores[k], parent=w, title=T(txt))
                    if elegido and elegido[1]:
                        colores[k] = str(elegido[1]).lower()
                        pintar()
                        guardar()
                ttk.Button(f, text=T("Cambiar…"), style="Small.TButton", command=cambiar).grid(
                    row=i, column=2, sticky="w")
            var = tk.BooleanVar(value=bool(colores["step"]))

            def cambio_step():
                colores["step"] = bool(var.get())
                guardar()
            ttk.Checkbutton(f, text=T("Usar los colores del STEP para la placa (si los trae)"),
                            variable=var, command=cambio_step).grid(row=3, column=0, columnspan=3,
                                                                    sticky="w", pady=(10, 0))
            ttk.Label(f, text=T("Se aplican en el visor al momento."), style="Help.TLabel").grid(
                row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

            def restablecer():
                colores.update(COLORES_FC)
                var.set(True)
                pintar()
                guardar()
            b = ttk.Frame(f)
            b.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
            ttk.Button(b, text=T("Restablecer"), command=restablecer).pack(side="left")
            ttk.Button(b, text=T("Cerrar"), style="Primary.TButton", command=w.destroy).pack(side="left", padx=(8, 0))
            self.dialogo_color = w
            return w

        # ------------------------------------------------- piezas de la interfaz
        def _var(self, clave, valor=""):
            """StringVar de un campo de la configuración; cada cambio dispara la validación en vivo."""
            v = tk.StringVar(value=valor)
            v.trace_add("write", lambda *_: self._cambio())
            self.v[clave] = v
            return v

        def _tarjeta(self, padre, titulo, pady=(0, 10)):
            """Grupo con título. Columnas: etiqueta | valor | unidad | ayuda."""
            g = ttk.LabelFrame(padre, text=titulo, padding=(12, 6, 12, 10))
            g.pack(fill="x", pady=pady)
            g.columnconfigure(0, minsize=170)
            g.columnconfigure(3, weight=1)
            return g

        def _ayuda(self, padre, texto, fila=None, estilo="Help.TLabel", ancho=500):
            """Texto de ayuda breve (gris); con `fila` se coloca en la rejilla de una tarjeta."""
            lbl = ttk.Label(padre, text=texto, style=estilo, wraplength=ancho, justify="left")
            if fila is None:
                lbl.pack(anchor="w", fill="x", pady=(0, 8))
            else:
                lbl.grid(row=fila, column=0, columnspan=4, sticky="w", pady=(4, 0))
            return lbl

        def _fila(self, padre, fila, clave, texto=None, ayuda="", ancho=9):
            """Fila etiqueta | valor | unidad | ayuda para un campo numérico. Devuelve el Entry."""
            ttk.Label(padre, text=texto or etiqueta(clave)).grid(row=fila, column=0, sticky="w", pady=3)
            e = ttk.Entry(padre, textvariable=self._var(clave), width=ancho, justify="right")
            e.grid(row=fila, column=1, sticky="w", pady=3)
            ttk.Label(padre, text="°" if clave == "rotation" else "mm", style="Help.TLabel").grid(
                row=fila, column=2, sticky="w", padx=(4, 10))
            if ayuda:
                ttk.Label(padre, text=ayuda, style="Help.TLabel", wraplength=230,
                          justify="left").grid(row=fila, column=3, sticky="w")
            return e

        def _radios(self, padre, var, opciones, fila0=0):
            """Grupo de botones de opción en una tarjeta. Devuelve la lista de botones."""
            botones = []
            for i, (txt, val) in enumerate(opciones):
                rb = ttk.Radiobutton(padre, text=txt, value=val, variable=var)
                rb.grid(row=fila0 + i, column=0, columnspan=4, sticky="w", pady=1)
                botones.append(rb)
            return botones

        def _seccion(self, padre, titulo, abierta=True):
            """Sección plegable: devuelve el marco de su contenido."""
            marco = ttk.Frame(padre)
            marco.pack(fill="x", pady=(0, 8))
            cuerpo = ttk.Frame(marco, padding=(16, 6, 4, 4))
            estado = {"abierta": abierta}
            btn = ttk.Button(marco, style="Section.TButton")

            def pintar():
                btn.configure(text=("▾   " if estado["abierta"] else "▸   ") + T(titulo))
                if estado["abierta"]:
                    cuerpo.pack(fill="x")
                else:
                    cuerpo.pack_forget()

            def alternar():
                estado["abierta"] = not estado["abierta"]
                pintar()
            btn.configure(command=alternar)
            btn.pack(fill="x")
            pintar()
            self._repintar.append(pintar)
            return cuerpo

        def _marco(self, nb, titulo):
            """Página desplazable de una pestaña. Devuelve el marco donde va el contenido."""
            pagina = ttk.Frame(nb)
            nb.add(pagina, text=titulo)
            canvas = tk.Canvas(pagina, bg=UI["bg"], highlightthickness=0, width=550)
            barra = ttk.Scrollbar(pagina, orient="vertical", command=canvas.yview)
            barra.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=barra.set)
            f = ttk.Frame(canvas, padding=(12, 12, 14, 12))
            contenido = canvas.create_window((0, 0), window=f, anchor="nw")
            f.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(contenido, width=e.width))
            def rueda(e):
                if str(e.widget).startswith(str(f)) or e.widget == canvas:
                    canvas.yview_scroll(-int(e.delta/120), "units")
                    return "break"
            self.bind("<MouseWheel>", rueda, add="+")
            return f

        # ------------------------------------------------------------ pestañas
        def _tab_placa(self, nb):
            """Pestaña Placa: origen (STEP o manual), orientación, medida usada y cara inferior del PCB."""
            f = self._marco(nb, "Placa")
            self._var("source", "manual")
            g = self._tarjeta(f, "Origen de la placa")
            ttk.Radiobutton(g, text="Importar placa STEP (.step / .stp)", value="step",
                            variable=self.v["source"]).grid(row=0, column=0, columnspan=4, sticky="w")
            fila = ttk.Frame(g)
            fila.grid(row=1, column=0, columnspan=4, sticky="we", padx=(22, 0), pady=(2, 6))
            self.e_step = ttk.Entry(fila, textvariable=self._var("step_path"))
            self.e_step.pack(side="left", fill="x", expand=True)
            ttk.Button(fila, text="Examinar…", command=self.elegir_step).pack(side="left", padx=(6, 0))
            ttk.Radiobutton(g, text="Medidas manuales (la placa es un bloque)", value="manual",
                            variable=self.v["source"]).grid(row=2, column=0, columnspan=4, sticky="w")
            ttk.Label(g, text="Eje vertical del STEP").grid(row=3, column=0, sticky="w", pady=(8, 3))
            self.cb_eje = ttk.Combobox(g, textvariable=self._var("up_axis", "Z"),
                                       values=("Z", "Y", "X"), state="readonly", width=6)
            self.cb_eje.grid(row=3, column=1, sticky="w", pady=(8, 3))
            ttk.Label(g, text="Giro en torno a Z").grid(row=4, column=0, sticky="w", pady=3)
            fr = ttk.Frame(g)
            fr.grid(row=4, column=1, columnspan=3, sticky="w")
            ttk.Entry(fr, textvariable=self._var("rotation", "0"), width=7, justify="right").pack(side="left")
            ttk.Label(fr, text="°", style="Help.TLabel").pack(side="left", padx=(4, 8))
            for a in (0, 90, 180, 270):
                ttk.Button(fr, text=f"{a}°", width=4, style="Small.TButton",
                           command=lambda a=a: self.v["rotation"].set(str(a))).pack(side="left", padx=1)
            self.btn_medir = ttk.Button(g, text="Medir STEP con FreeCAD", command=self.medir)
            self.btn_medir.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
            self.lbl_medida = ttk.Label(g, text="", style="Help.TLabel", wraplength=500, justify="left")
            self.lbl_medida.grid(row=6, column=0, columnspan=4, sticky="w", pady=(6, 0))

            m = self._tarjeta(f, "Medidas que dimensionan la caja")
            fit = self._var("board_fit", "pcb")
            fit.trace_add("write", lambda *_: self._cambio_fit())
            self.rb_fit = self._radios(m, fit, (
                ("Solo el PCB, sin componentes (recomendado)", "pcb"),
                ("Envolvente con todos los componentes", "total"),
                ("Personalizadas", "personalizado")))
            self.e_board = [self._fila(m, 3, "board_x", "Ancho X"),
                            self._fila(m, 4, "board_y", "Largo Y"),
                            self._fila(m, 5, "board_z", "Alto Z total")]
            self._ayuda(m, "Con «Solo el PCB», lo que sobresale (p. ej. conectores laterales) atraviesa "
                        "la pared: se avisa y se puede detectar en Aberturas. Las personalizadas se "
                        "centran en el PCB.", fila=6)

            p = self._tarjeta(f, "Cara inferior del PCB")
            self._fila(p, 0, "pcb_offset", "Desde el punto más bajo", "Se rellena al medir el STEP.")
            self.lbl_sug = ttk.Label(p, text="", style="Help.TLabel")
            self.lbl_sug.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        def _tab_caja(self, nb):
            """Pestaña Caja: holguras, paredes y apoyo de la placa."""
            f = self._marco(nb, "Caja")
            g = self._tarjeta(f, "Holguras alrededor de la placa")
            self._fila(g, 0, "margin", "Holgura lateral", "Por lado. 0 = placa a presión.")
            self._fila(g, 1, "bottom_gap", "Holgura inferior", "Bajo el punto más bajo.")
            self._fila(g, 2, "top_gap", "Holgura superior", "Sobre el componente más alto.")
            g = self._tarjeta(f, "Paredes")
            self._fila(g, 0, "wall", "Pared lateral", "≥ 3 mm con lengüeta o labio.")
            self._fila(g, 1, "floor", "Piso")
            self._fila(g, 2, "lid", "Techo de la tapa")
            self._fila(g, 3, "radius", "Radio de esquinas", "Exterior.")
            g = self._tarjeta(f, "Apoyo de la placa")
            self._var("board_rest", "esquinas")
            self._radios(g, self.v["board_rest"], (
                ("Apoyos en las esquinas bajo el PCB (recomendado)", "esquinas"),
                ("Repisa a lo largo del borde del PCB", "repisa"),
                ("Sin apoyos", "ninguno")))
            self._fila(g, 3, "rest_width", "Ancho bajo el PCB", "Cuánto entra bajo el borde.")
            self.var_pisadores = tk.BooleanVar(value=False)
            self.var_pisadores.trace_add("write", lambda *_: self._cambio())
            ttk.Checkbutton(g, text="Pisadores en la tapa (sujetan el PCB desde arriba)",
                            variable=self.var_pisadores).grid(row=4, column=0, columnspan=4, sticky="w",
                                                             pady=(6, 0))
            self._ayuda(g, "Los apoyos suben hasta la cara inferior del PCB para que no flote ni se "
                        "incline; donde hay componentes debajo se recortan solos.", fila=5)
            self._ayuda(f, "Coordenadas: X/Y desde el centro de la caja; Z desde la base exterior.")

        def _tab_cierre(self, nb):
            """Pestaña Cierre: tipo de cierre, altura de la unión, lengüeta/labio e imanes."""
            f = self._marco(nb, "Cierre")
            self._var("closure", "friccion")
            g = self._tarjeta(f, "Tipo de cierre (sin tornillos)")
            self._radios(g, self.v["closure"], (
                ("Lengüetas cortas con nervios de fricción", "friccion"),
                ("Labio continuo a presión", "labio"),
                ("Imanes en las esquinas + labio de alineación", "imanes"),
                ("Tornillos autorroscantes (directo al plástico)", "tornillos"),
                ("Tornillos con insertos roscados", "insertos"),
                ("Tapa apoyada (sin retención)", "apoyada")))
            g = self._tarjeta(f, "Unión entre base y tapa")
            self._fila(g, 0, "split_height", "Altura de la unión", "Desde la base exterior; 0 = automática.")
            ttk.Button(g, text="A la altura de las aberturas laterales", style="Small.TButton",
                       command=self._union_aberturas).grid(row=1, column=0, columnspan=4, sticky="w",
                                                           pady=(2, 6))
            self._fila(g, 2, "lid_depth", "Falda de la tapa", "Solo con unión automática.")
            self._ayuda(g, "A la altura de los conectores, sus aberturas quedan mitad en la base y mitad "
                        "en la tapa: la placa entra en vertical.", fila=3)
            # Secciones que dependen del cierre elegido (se muestran solo si aplican).
            cont = ttk.Frame(f)
            cont.pack(fill="x")
            self._sec_encaje = ttk.LabelFrame(cont, text="Lengüeta o labio", padding=(12, 6, 12, 10))
            self._sec_encaje.columnconfigure(0, minsize=170)
            self._sec_encaje.columnconfigure(3, weight=1)
            self._fila(self._sec_encaje, 0, "tongue_height", "Altura", "Entre 1 y 4 mm.")
            self._fila(self._sec_encaje, 1, "clearance", "Juego por lado", "0,10–0,25 típico en FDM.")
            self._fila(self._sec_encaje, 2, "rib_interference", "Interferencia de nervios",
                       "Por lado; 0 los desactiva.")
            self._sec_imanes = ttk.LabelFrame(cont, text="Imanes (cilindro plano)", padding=(12, 6, 12, 10))
            self._sec_imanes.columnconfigure(0, minsize=170)
            self._sec_imanes.columnconfigure(3, weight=1)
            self._fila(self._sec_imanes, 0, "magnet_diameter", "Diámetro")
            self._fila(self._sec_imanes, 1, "magnet_height", "Alto")
            self._fila(self._sec_imanes, 2, "magnet_clearance", "Holgura del alojamiento",
                       "Por lado; 0,1–0,2 a presión.")
            self._ayuda(self._sec_imanes, "4 en la base y 4 en la tapa, en torres que sobresalen en las "
                        "esquinas. Colócalos de modo que cada par se atraiga.", fila=3)
            self._sec_tornillos = ttk.LabelFrame(cont, text="Tornillos", padding=(12, 6, 12, 10))
            t = self._sec_tornillos
            t.columnconfigure(0, minsize=170)
            t.columnconfigure(3, weight=1)
            ttk.Label(t, text="Métrica").grid(row=0, column=0, sticky="w", pady=3)
            ttk.Combobox(t, textvariable=self._var("screw_size", "M3"), values=tuple(M.SCREWS),
                         state="readonly", width=7).grid(row=0, column=1, sticky="w", pady=3)
            self._fila(t, 1, "screw_length", "Largo del tornillo", "Bajo la cabeza.")
            ttk.Label(t, text="Cabeza").grid(row=2, column=0, sticky="w", pady=3)
            cab = ttk.Frame(t)
            cab.grid(row=2, column=1, columnspan=3, sticky="w")
            self._var("screw_head", "cilindrica")
            for txt, val in (("Cilíndrica (allen)", "cilindrica"), ("Avellanada", "avellanada")):
                ttk.Radiobutton(cab, text=txt, value=val, variable=self.v["screw_head"]).pack(side="left",
                                                                                          padx=(0, 12))
            self._fila(t, 3, "screw_hole", "Agujero en la base", "0 = típico de la métrica.")
            self._e_inserto = self._fila(t, 4, "insert_depth", "Largo del inserto", "0 = típico.")
            self.lbl_tornillo = ttk.Label(t, text="", style="Help.TLabel", wraplength=480, justify="left")
            self.lbl_tornillo.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
            self._ayuda(t, "Cuatro tornillos en torres de esquina (no quitan espacio a la placa): pasan "
                        "por la tapa y se anclan en la base.", fila=6)
            self._ayuda(f, "El ajuste real depende de la impresora: imprime una prueba y ajusta el juego.",
                        estilo="Warn.TLabel")
            self.v["closure"].trace_add("write", lambda *_: self._secciones_cierre())
            self._secciones_cierre()

        def _secciones_cierre(self):
            """Muestra solo las secciones que aplican al cierre elegido."""
            cierre = self.v["closure"].get()
            for sec in (self._sec_encaje, self._sec_imanes, self._sec_tornillos):
                sec.pack_forget()
            if cierre in M.LIP_CLOSURES:
                self._sec_encaje.pack(fill="x", pady=(0, 10))
            if cierre == "imanes":
                self._sec_imanes.pack(fill="x", pady=(0, 10))
            if cierre in M.SCREW_CLOSURES:
                self._sec_tornillos.pack(fill="x", pady=(0, 10))
                # El largo del inserto solo aplica con insertos.
                self._e_inserto.configure(state="normal" if cierre == "insertos" else "disabled")
            self._pista_tornillo()

        def _pista_tornillo(self, cfg=None):
            """Medidas que resultan para los tornillos: agujero en la base y largos que sirven."""
            if not hasattr(self, "lbl_tornillo"):
                return
            try:
                cfg = cfg or M.config_from(dict(self._crudo(), step_path=""))
                if cfg["closure"] not in M.SCREW_CLOSURES:
                    self.lbl_tornillo.configure(text="")
                    return
                f = M.screw_fit(cfg)
            except Exception:
                self.lbl_tornillo.configure(text="")
                return
            en = I.actual() == "en"
            inserto = cfg["closure"] == "insertos"
            if en:
                txt = (f"Base hole Ø{fmt(f['hole_d'])} mm" + (f" for a {fmt(f['ins_l'])} mm insert" if inserto else "")
                       + f". Usable screw length: {fmt(round(f['min_len'], 1))} to {fmt(round(f['max_len'], 1))} mm.")
            else:
                txt = (f"Agujero en la base Ø{fmt(f['hole_d'])} mm" + (f" para un inserto de {fmt(f['ins_l'])} mm"
                       if inserto else "") + f". Sirven tornillos de {fmt(round(f['min_len'], 1))} a "
                       f"{fmt(round(f['max_len'], 1))} mm.")
            self.lbl_tornillo.configure(text=txt)

        def _tabla(self, padre, columnas, alto=6):
            """Tabla con filas alternadas y barra de desplazamiento."""
            marco = ttk.Frame(padre)
            marco.pack(fill="x")
            tv = ttk.Treeview(marco, columns=[c for c, _, _ in columnas], show="headings",
                              height=alto, selectmode="browse")
            for c, t, w in columnas:
                tv.heading(c, text=t)
                tv.column(c, width=w, anchor="center", stretch=True)
            tv.tag_configure("par", background=UI["stripe"])
            barra = ttk.Scrollbar(marco, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=barra.set)
            tv.pack(side="left", fill="x", expand=True)
            barra.pack(side="left", fill="y")
            return tv

        def _tab_aberturas(self, nb):
            """Pestaña Aberturas: tabla, edición en vivo, detección en paredes y aberturas de tapa."""
            f = self._marco(nb, "Aberturas")
            self.tv_ports = self._tabla(f, [("side", "Cara", 115), ("shape", "Forma", 120), ("u", "u", 55),
                                            ("v", "v", 55), ("width", "Ancho", 60), ("height", "Alto", 60)])
            self.tv_ports.bind("<<TreeviewSelect>>", lambda e: self._sel_tabla("ports"))
            self._botones_tabla(f, "ports")
            self.pv = {k: tk.StringVar() for k in ("side", "shape", "u", "v", "width", "height", "radius")}
            self.pv["to_seam"] = tk.BooleanVar(value=False)
            self.pv["side"].set(T(LADOS[0][0]))
            self.pv["shape"].set(T(FORMAS[0][0]))
            self.pv["radius"].set("1")

            g = self._seccion(f, "Editar abertura")
            g.columnconfigure(1, weight=1)
            g.columnconfigure(3, weight=1)
            ttk.Label(g, text="Cara").grid(row=0, column=0, sticky="w", pady=3)
            cb = ttk.Combobox(g, textvariable=self.pv["side"], values=[T(t) for t, _ in LADOS],
                              state="readonly", width=24)
            cb.grid(row=0, column=1, columnspan=3, sticky="w", pady=3, padx=(8, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self._pista_puerto())
            ttk.Label(g, text="Forma").grid(row=1, column=0, sticky="w", pady=3)
            ttk.Combobox(g, textvariable=self.pv["shape"], values=[T(t) for t, _ in FORMAS],
                         state="readonly", width=24).grid(row=1, column=1, columnspan=3, sticky="w",
                                                          pady=3, padx=(8, 0))
            for i, (k, t) in enumerate((("u", "u"), ("v", "v"), ("width", "Ancho / Ø"),
                                        ("height", "Alto"), ("radius", "Radio de esquinas"))):
                fila, col = 2 + i // 2, (i % 2) * 2
                ttk.Label(g, text=t).grid(row=fila, column=col, sticky="w", pady=3)
                ttk.Entry(g, textvariable=self.pv[k], width=9, justify="right").grid(
                    row=fila, column=col + 1, sticky="w", padx=(8, 16), pady=3)
            ttk.Checkbutton(g, text="Abrir hasta la unión (entra en vertical; la tapa la cierra)",
                            variable=self.pv["to_seam"]).grid(row=5, column=0, columnspan=4, sticky="w",
                                                             pady=(4, 0))
            for var in self.pv.values():
                var.trace_add("write", lambda *_: self._editar_en_vivo("ports"))
            self.lbl_pista_p = ttk.Label(g, text="", style="Help.TLabel", wraplength=480, justify="left")
            self.lbl_pista_p.grid(row=6, column=0, columnspan=4, sticky="w", pady=(6, 0))
            self._ayuda(g, "Laterales: u = posición horizontal desde el centro de la cara; v = altura "
                        "desde la base exterior. Tapa: u = X, v = Y. Con una fila seleccionada, los "
                        "cambios se aplican a ella.", fila=7)

            d = self._seccion(f, "Detectar aberturas en las paredes", abierta=False)
            self._ayuda(d, "Busca los componentes que atraviesan la caja (conectores laterales) y agrega "
                        "una abertura para cada uno. Las que ya existen no se repiten.")
            fila = ttk.Frame(d)
            fila.pack(fill="x")
            ttk.Label(fila, text="Juego alrededor").pack(side="left")
            self.var_det_margen = tk.StringVar(value="0,5")
            ttk.Entry(fila, textvariable=self.var_det_margen, width=6, justify="right").pack(side="left",
                                                                                          padx=(8, 4))
            ttk.Label(fila, text="mm", style="Help.TLabel").pack(side="left")
            self.var_det_union = tk.BooleanVar(value=False)
            ttk.Checkbutton(d, text="Abrirlas hasta la unión (insertar la placa en vertical)",
                            variable=self.var_det_union).pack(anchor="w", pady=(6, 0))
            self.btn_detectar = ttk.Button(d, text="Detectar aberturas", command=self.detectar_aberturas)
            self.btn_detectar.pack(anchor="w", pady=(8, 0))

            t = self._seccion(f, "Aberturas en la tapa sobre componentes", abierta=False)
            self._ayuda(t, "Elige de una lista los componentes a los que quieres llegar desde arriba "
                        "(conectores, trimmers…). Vienen marcados los que lo parecen por su nombre. "
                        "La abertura sigue la planta del componente.")
            self.btn_componentes = ttk.Button(t, text="Elegir componentes…",
                                              command=self.elegir_componentes_tapa)
            self.btn_componentes.pack(anchor="w")

        def _tab_soportes(self, nb):
            """Pestaña Soportes: tabla de postes y su formulario."""
            f = self._marco(nb, "Soportes")
            self._ayuda(f, "Postes cilíndricos sobre el piso. x, y desde el centro de la caja; altura "
                        "desde la cara superior del piso. Agujero 0 = macizo (no perfora el piso).")
            self.tv_supports = self._tabla(f, [("x", "x", 80), ("y", "y", 80), ("height", "Altura", 90),
                                               ("diameter", "Diámetro", 90), ("hole", "Agujero", 90)])
            self.tv_supports.bind("<<TreeviewSelect>>", lambda e: self._sel_tabla("supports"))
            self._botones_tabla(f, "supports")
            g = self._tarjeta(f, "Editar soporte", pady=(4, 10))
            self.sv = {k: tk.StringVar() for k in ("x", "y", "height", "diameter", "hole")}
            self.sv["diameter"].set("6")
            self.sv["hole"].set("2.5")
            for var in self.sv.values():
                var.trace_add("write", lambda *_: self._editar_en_vivo("supports"))
            for i, (k, t) in enumerate((("x", "x"), ("y", "y"), ("height", "Altura"),
                                        ("diameter", "Diámetro"), ("hole", "Agujero Ø"))):
                fila, col = i // 2, (i % 2) * 2
                ttk.Label(g, text=t).grid(row=fila, column=col, sticky="w", pady=3)
                ttk.Entry(g, textvariable=self.sv[k], width=9, justify="right").grid(
                    row=fila, column=col + 1, sticky="w", padx=(8, 16), pady=3)
            ttk.Button(g, text="Altura sugerida", style="Small.TButton",
                       command=self._altura_sugerida).grid(row=2, column=2, columnspan=2, sticky="w")
            self._ayuda(g, "La colisión con la placa se comprueba al calcular en FreeCAD.", fila=3)

        def _botones_tabla(self, padre, tipo):
            """Botones Agregar / Actualizar / Eliminar / Deseleccionar de una tabla."""
            b = ttk.Frame(padre)
            b.pack(fill="x", pady=(6, 10))
            ttk.Button(b, text="Agregar", style="Small.TButton",
                       command=lambda: self._editar_tabla(tipo, "add")).pack(side="left")
            ttk.Button(b, text="Actualizar", style="Small.TButton",
                       command=lambda: self._editar_tabla(tipo, "upd")).pack(side="left", padx=4)
            ttk.Button(b, text="Eliminar", style="Small.TButton",
                       command=lambda: self._editar_tabla(tipo, "del")).pack(side="left")
            ttk.Button(b, text="Deseleccionar", style="Small.TButton",
                       command=lambda: self._editar_tabla(tipo, "none")).pack(side="right")

        def _tab_exportar(self, nb):
            """Pestaña Exportar: nombre, generación en carpeta nueva y macro autónoma."""
            f = self._marco(nb, "Exportar")
            g = self._tarjeta(f, "Proyecto")
            ttk.Label(g, text="Nombre").grid(row=0, column=0, sticky="w")
            ttk.Entry(g, textvariable=self._var("name", "Caja_circuito"), width=32).grid(
                row=0, column=1, columnspan=3, sticky="w")
            g = self._tarjeta(f, "Generar modelos con FreeCAD")
            self.var_stl = tk.BooleanVar(value=True)
            self.var_step = tk.BooleanVar(value=False)
            for var in (self.var_stl, self.var_step):
                var.trace_add("write", lambda *_: self._cambio())
            ttk.Checkbutton(g, text="STL de base y tapa (orientados para imprimir)",
                            variable=self.var_stl).grid(row=0, column=0, columnspan=4, sticky="w")
            ttk.Checkbutton(g, text="STEP de base y tapa", variable=self.var_step).grid(
                row=1, column=0, columnspan=4, sticky="w")
            ttk.Label(g, text="Carpeta de salida").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 2))
            fila = ttk.Frame(g)
            fila.grid(row=3, column=0, columnspan=4, sticky="we")
            self.var_salida = tk.StringVar(value=self.ajustes.get("salida", str(AQUI / "cajas_generadas")))
            ttk.Entry(fila, textvariable=self.var_salida).pack(side="left", fill="x", expand=True)
            ttk.Button(fila, text="…", width=3, command=self.elegir_salida).pack(side="left", padx=(6, 0))
            self._ayuda(g, "Cada generación crea una subcarpeta nueva (nombre_fecha_hora) con Caja.FCStd, "
                        "las mallas, el proyecto y la macro. Nunca se sobrescribe nada.", fila=4)
            botones = ttk.Frame(g)
            botones.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
            self.btn_generar = ttk.Button(botones, text="Generar en carpeta nueva", style="Primary.TButton",
                                          command=self.generar)
            self.btn_generar.pack(side="left")
            self.btn_abrir_salida = ttk.Button(botones, text="Abrir última carpeta", state="disabled",
                                               command=self._abrir_ultima)
            self.btn_abrir_salida.pack(side="left", padx=8)
            self.ultima_salida = None

            g = self._tarjeta(f, "Macro autónoma de FreeCAD")
            self._ayuda(g, "Un .FCMacro con el constructor y la configuración: en FreeCAD crea un documento "
                        "nuevo con base, tapa y placa. Para cambiar la caja, edita CONFIG y vuelve a "
                        "ejecutarla.", fila=0)
            ttk.Button(g, text="Exportar macro…", command=self.exportar_macro).grid(
                row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        def _panel_visor(self, padre):
            """Panel derecho: estado y botones del visor, vista, transparencias y avisos."""
            g = ttk.LabelFrame(padre, text="Visor 3D · FreeCAD", padding=(14, 8, 14, 12))
            g.pack(fill="both", expand=True)
            fila = ttk.Frame(g)
            fila.pack(fill="x")
            self.lbl_visor = ttk.Label(fila, text=ESTADOS_VISOR["detenida"][0], style="Muted.TLabel",
                                       font=self.fuentes["title"])
            self.lbl_visor.pack(side="left")
            self.btn_visor_cerrar = ttk.Button(fila, text="Cerrar", style="Small.TButton",
                                               command=self.cerrar_visor)
            self.btn_visor_cerrar.pack(side="right")
            self.btn_visor_reiniciar = ttk.Button(fila, text="Reiniciar", style="Small.TButton",
                                                  command=self.reiniciar_visor)
            self.btn_visor_reiniciar.pack(side="right", padx=4)
            self.btn_visor_abrir = ttk.Button(fila, text="Abrir visor", style="Small.TButton",
                                              command=self.abrir_visor)
            self.btn_visor_abrir.pack(side="right")
            ttk.Label(g, text="Ventana de FreeCAD aparte con los sólidos reales: gira y acerca con el ratón "
                      "allí. No guarda nada; exporta desde la pestaña Exportar.", style="Help.TLabel",
                      wraplength=640, justify="left").pack(anchor="w", pady=(4, 10))
            acciones = ttk.Frame(g)
            acciones.pack(fill="x")
            self.btn_vista = ttk.Button(acciones, text="Calcular caja en FreeCAD", style="Primary.TButton",
                                        command=self.vista_previa)
            self.btn_vista.pack(side="left")
            self.var_auto = tk.BooleanVar(value=bool(self.ajustes.get("auto_visor", True)))
            self.var_auto.trace_add("write", lambda *_: self._cambio_auto())
            ttk.Checkbutton(acciones, text="Actualizar automáticamente",
                            variable=self.var_auto).pack(side="left", padx=14)
            self.lbl_vista = ttk.Label(g, text="", style="Muted.TLabel", wraplength=640, justify="left")
            self.lbl_vista.pack(fill="x", pady=(6, 0))

            v = ttk.LabelFrame(g, text="Vista", padding=(12, 6, 12, 10))
            v.pack(fill="x", pady=(10, 10))
            v.columnconfigure(1, weight=1)
            modos = ttk.Frame(v)
            modos.grid(row=0, column=0, columnspan=3, sticky="we")
            self._var("view", "separada")
            self.v["view"].trace_add("write", lambda *_: self._programar_vista())
            for t, k in VISTAS:
                ttk.Radiobutton(modos, text=t, value=k, variable=self.v["view"]).pack(side="left", padx=(0, 12))
            ttk.Button(modos, text="Encuadrar", style="Small.TButton",
                       command=lambda: self._programar_vista(encuadrar=True)).pack(side="right")
            vis = ttk.Frame(v)
            vis.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))
            ttk.Label(vis, text="Mostrar", style="Muted.TLabel").pack(side="left", padx=(0, 10))
            self.ver = {k: tk.BooleanVar(value=True) for k in ("base", "lid", "board")}
            for k, t in (("base", "Base"), ("lid", "Tapa"), ("board", "Placa")):
                ttk.Checkbutton(vis, text=t, variable=self.ver[k],
                                command=self._programar_vista).pack(side="left", padx=(0, 10))
            self.transp = {k: tk.IntVar(value=0) for k in ("base", "lid")}
            for i, (k, t) in enumerate((("base", "Transparencia base"), ("lid", "Transparencia tapa"))):
                ttk.Label(v, text=t, style="Muted.TLabel").grid(row=2 + i, column=0, sticky="w", pady=2)
                lbl = ttk.Label(v, text="0 %", width=5, anchor="e")
                escala = ttk.Scale(v, from_=0, to=90, orient="horizontal",
                                   command=lambda val, k=k, lbl=lbl: self._cambio_transp(k, val, lbl))
                escala.grid(row=2 + i, column=1, sticky="we", padx=10)
                lbl.grid(row=2 + i, column=2, sticky="e")

            a = ttk.LabelFrame(g, text="Avisos y resultado", padding=(10, 6, 10, 8))
            a.pack(fill="both", expand=True)
            self.warn_box = tk.Text(a, height=7, bg=UI["panel"], fg=UI["text"], relief="flat",
                                    wrap="word", font=self.fuentes["small"], insertbackground=UI["text"],
                                    padx=8, pady=6, spacing1=2, spacing3=2, highlightthickness=0,
                                    borderwidth=0)
            self.warn_box.tag_configure("aviso", foreground=UI["warn"], lmargin2=22)
            self.warn_box.tag_configure("error", foreground=UI["error"], lmargin2=22)
            self.warn_box.tag_configure("info", foreground=UI["muted"], lmargin2=22)
            self.warn_box.tag_configure("detalle", foreground=UI["muted"], lmargin1=22, lmargin2=22)
            self.warn_box.pack(fill="both", expand=True)
            self.warn_box.configure(state="disabled")
            self.lbl_info = ttk.Label(a, text="", style="Help.TLabel", font=self.fuentes["mono_small"], justify="left")
            self.lbl_info.pack(anchor="w", pady=(6, 0))

        # ------------------------------------------------------------ visor
        def _exe_freecad(self):
            """freecad.exe que corresponde al Python de FreeCAD configurado (o None)."""
            return S.freecad_desde(self.python_fc) if self.python_fc else None

        def _obtener_sesion(self):
            """Sesión del visor (la crea si hace falta). None si no se encuentra FreeCAD."""
            exe = self._exe_freecad()
            if exe is None:
                messagebox.showerror("FreeCAD no encontrado",
                                     "No encuentro freecad.exe. Indícalo con «Python de FreeCAD…» "
                                     "(sirve el python.exe o el freecad.exe de su carpeta bin).", parent=self)
                return None
            if self.sesion is None or (not self.sesion.vivo() and Path(self.sesion.freecad_exe) != exe):
                self.sesion = S.SesionFreeCAD(exe)
            return self.sesion

        def _visor_vivo(self):
            """True si el proceso del visor está en marcha."""
            return self.sesion is not None and self.sesion.vivo()

        def _libre(self):
            """True si no hay ninguna operación en curso (worker ni visor) y la interfaz no está bloqueada."""
            return not (self._ui_bloqueada or self.trab.ocupado or self.tvisor.ocupado)

        def _lanzar_visor(self, accion, pasos, etiqueta_, bloquear=True):
            """Lanza una operación con el visor; con bloquear=True deshabilita la interfaz mientras dura."""
            if not self._libre():
                self.lbl_estado.configure(text="Espera a que termine la operación en curso.",
                                          style="Warn.TLabel")
                self.cadena = []
                return False
            ses = self._obtener_sesion()
            if ses is None:
                self.cadena = []
                return False
            if bloquear:
                self._ocupado(True, etiqueta_)
            self.tvisor.lanzar(ses, pasos, accion, etiqueta_)
            return True

        def abrir_visor(self):
            """Abre (o reutiliza) la ventana del visor FreeCAD."""
            self._lanzar_visor("v_abrir", lambda s: s.iniciar(), "Abriendo el visor FreeCAD")

        def _fin_v_abrir(self, r):
            """Resultado de abrir el visor."""
            self.lbl_estado.configure(text="Visor FreeCAD listo" + (" (sesión existente)." if r == "reutilizada"
                                                                   else "."), style="Ok.TLabel")
            self._actualizar_visor()

        def cerrar_visor(self):
            """Cierre ordenado del visor."""
            if self._visor_vivo():
                self._lanzar_visor("v_cerrar", lambda s: s.detener(), "Cerrando el visor FreeCAD")

        def _fin_v_cerrar(self, r):
            """Tras cerrar el visor ya no se muestra ninguna caja."""
            self.vista_clave = None
            self._estado_vista()
            self._actualizar_visor()

        def reiniciar_visor(self):
            """Recuperación: termina el proceso del visor (aunque esté calculando) y abre otro."""
            if not self._visor_vivo() and not self.tvisor.ocupado:
                self.abrir_visor()
                return
            if not messagebox.askyesno(
                    "Reiniciar visor",
                    "Se cerrará la ventana de FreeCAD del visor, con la placa en memoria y cualquier cálculo "
                    "en curso, y se abrirá otra. Tus proyectos y archivos no se tocan. ¿Continuar?",
                    parent=self):
                return
            self.cadena = []
            self._reiniciando = True
            self.tvisor.cancelar()
            self.sesion.matar_sin_esperar()
            self.vista_clave = None
            self._estado_vista()
            self._cuando_libre(self.abrir_visor)

        def _cuando_libre(self, accion, intentos=200):
            """Ejecuta `accion` en cuanto termine la operación en curso (con un límite de intentos)."""
            if self.tvisor.ocupado or self.trab.ocupado:
                if intentos > 0:
                    self.after(100, lambda: self._cuando_libre(accion, intentos - 1))
                return
            self._reiniciando = False
            if self._ui_bloqueada:
                self._ocupado(False)
            accion()

        def _sondear_visor(self):
            """Sondeo periódico (1 s) del estado del visor."""
            try:
                self._actualizar_visor()
            finally:
                self.after(1000, self._sondear_visor)

        def _actualizar_visor(self):
            """Muestra el estado del visor (conectado, calculando, cerrado…) y ajusta sus botones."""
            st = self.sesion.comprobar() if self.sesion else "detenida"
            texto, estilo = ESTADOS_VISOR.get(st, ESTADOS_VISOR["detenida"])
            texto = T(texto)
            self.lbl_visor.configure(text=texto, style=estilo)
            self.btn_visor_abrir.configure(
                text=T("Reconectar") if st in ("cerrada", "sin_respuesta") else T("Abrir visor"))
            if st in ("cerrada", "detenida") and self.vista_clave is not None:
                self.vista_clave = None
                self._estado_vista()

        def _datos_vista(self):
            """Vista, visibilidad, transparencias y colores que se envían al visor."""
            return {"vista": self.v["view"].get(),
                    "visible": {k: bool(var.get()) for k, var in self.ver.items()},
                    "transparencia": {k: int(var.get()) for k, var in self.transp.items()},
                    "colores": self._colores_fc()}

        def _cambio_transp(self, k, valor, lbl):
            """Cambio de un deslizador de transparencia: actualiza la etiqueta y programa la vista."""
            n = int(round(float(valor)))
            if n != self.transp[k].get():
                self.transp[k].set(n)
                lbl.configure(text=f"{n} %")
                self._programar_vista()

        def _programar_vista(self, encuadrar=False):
            """Programa el envío de la vista al visor (agrupa cambios rápidos)."""
            if self._cargando:
                return
            if self._tarea_vista:
                self.after_cancel(self._tarea_vista)
            self._tarea_vista = self.after(150, lambda: self._enviar_vista(encuadrar))

        def _enviar_vista(self, encuadrar=False):
            """Cambia vista/visibilidad sin recalcular. Si el visor está ocupado, los ajustes
            viajan con el próximo cálculo."""
            self._tarea_vista = None
            if not self._visor_vivo() or not self._libre():
                return
            datos = dict(self._datos_vista(), encuadrar=bool(encuadrar))
            self.tvisor.lanzar(self.sesion, lambda s: s.solicitar("vista", datos, plazo=60),
                               "v_vista", "Vista")

        def _fin_v_vista(self, r):
            """Resultado de aplicar la vista (sin diálogos)."""
            if not r.get("hay_caja"):
                self.lbl_estado.configure(text="Vista aplicada (todavía no hay caja calculada).",
                                          style="Muted.TLabel")

        def _clave_vista(self, cfg):
            # La vista, las opciones de exportación y la cara inferior del PCB (solo sugiere la
            # altura de soportes) no cambian la geometría.
            """Huella de la geometría de una configuración: dice si el visor muestra la caja actual."""
            return json.dumps({k: v for k, v in cfg.items()
                               if k not in ("view", "export_stl", "export_step", "name", "pcb_offset")},
                              sort_keys=True)

        def _info_visor(self, r, titulo):
            """Tiempos y lecturas del STEP de la última operación del visor."""
            cache = r.get("cache", {})
            t = r.get("tiempos", {})
            partes = [f"{titulo}: " + " · ".join(f"{k} {v:.2f} s" for k, v in t.items())]
            partes.append(f"Lecturas del STEP (Part.read) en esta sesión: {cache.get('read_count', 0)}"
                          + (" — se leyó ahora" if r.get("lectura_nueva") else " — placa en memoria"))
            if "board_reassigned" in r:
                partes.append("Placa en el visor: " + ("actualizada" if r["board_reassigned"]
                                                       else "reutilizada sin cambios"))
            self.lbl_info.configure(text="\n".join(partes))

        # ------------------------------------------------- datos y validación
        def _cambio(self):
            """Marca el proyecto como modificado y programa la validación en vivo."""
            if self._cargando:
                return
            self.modificado = True
            self._actualizar_titulo()
            if self._tarea_val:
                self.after_cancel(self._tarea_val)
            self._tarea_val = self.after(250, self._validar_en_vivo)

        def _crudo(self):
            """Configuración tal como está en los campos (sin validar)."""
            c = {k: self.v[k].get() for k in CAMPOS_NUM}
            c["name"] = self.v["name"].get().strip()
            c["step_path"] = self.v["step_path"].get().strip() if self.v["source"].get() == "step" else ""
            c["up_axis"] = self.v["up_axis"].get()
            c["board_fit"] = self.v["board_fit"].get()
            c["closure"] = self.v["closure"].get()
            c["view"] = self.v["view"].get()
            c["export_stl"] = bool(self.var_stl.get())
            c["export_step"] = bool(self.var_step.get())
            c["board_rest"] = self.v["board_rest"].get()
            c["screw_size"] = self.v["screw_size"].get()
            c["screw_head"] = self.v["screw_head"].get()
            c["hold_down"] = bool(self.var_pisadores.get())
            c["supports"] = [dict(s) for s in self.supports]
            c["ports"] = [dict(p) for p in self.ports]
            if c["step_path"] and self.medida and self.medida_clave == self._clave_step(c):
                medidas = self._medida_para(c["board_fit"])
                if medidas:
                    c["board_x"], c["board_y"], c["board_z"] = medidas
            return c

        def _medida_para(self, fit):
            """Medidas [x, y, z] del STEP medido para el modo elegido (None si son las
            personalizadas de los campos)."""
            if not self.medida or fit == "personalizado":
                return None
            sizes = self.medida.get("sizes") or {}
            total = sizes.get("total") or self.medida["size"]
            pcb = sizes.get("pcb")
            if fit == "pcb" and pcb:
                return [pcb[0], pcb[1], total[2]]
            return list(total)

        def _cambio_fit(self):
            """Al cambiar la medida de placa, los campos muestran la que se usará."""
            if self._cargando:
                return
            c = {"step_path": self.v["step_path"].get().strip() if self.v["source"].get() == "step" else "",
                 "up_axis": self.v["up_axis"].get(), "rotation": self.v["rotation"].get()}
            if c["step_path"] and self.medida and self.medida_clave == self._clave_step(c):
                self._poner_medidas(self._medida_para(self.v["board_fit"].get()))

        def _poner_medidas(self, medidas):
            """Escribe en los campos las medidas de la placa sin disparar la validación."""
            if not medidas:
                return
            cargando, self._cargando = self._cargando, True
            for k, val in zip(("board_x", "board_y", "board_z"), medidas):
                self.v[k].set(fmt(round(val, 4)))
            self._cargando = cargando

        def _clave_step(self, c):
            """Identifica la medición del STEP: ruta, eje, giro y firma del archivo (tamaño, fecha)."""
            try:
                giro = round(M.number(c["rotation"]), 6)
            except (ValueError, TypeError):
                giro = c["rotation"]
            try:
                stat = Path(c["step_path"]).stat()
                firma = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                firma = None
            return (c["step_path"], c["up_axis"], giro, firma)

        def _step_pendiente(self, c):
            """True si el STEP (o su orientación) cambió desde la última medición."""
            return bool(c["step_path"]) and self.medida_clave != self._clave_step(c)

        def _validar(self):
            """Devuelve la configuración validada o lanza ValueError."""
            c = self._crudo()
            if self._step_pendiente(c):
                raise ValueError("Mide el STEP (orientación actual) antes de validar.")
            return M.config_from(c)

        def _validar_en_vivo(self):
            """Valida mientras se escribe: medidas, avisos, pista de aberturas y actualización automática."""
            self._tarea_val = None
            modo_step = self.v["source"].get() == "step"
            fijas = modo_step and self.v["board_fit"].get() != "personalizado"
            for e in self.e_board:
                e.configure(state="disabled" if fijas or self._ui_bloqueada else "normal")
            for rb in self.rb_fit:
                rb.configure(state="normal" if modo_step and not self._ui_bloqueada else "disabled")
            c = self._crudo()
            try:
                sug = M.suggested_support_height(M.config_from(dict(c, step_path="")))
                self.lbl_sug.configure(
                    text=f"Suggested standoff height: {fmt(sug)} mm (bottom clearance + PCB bottom face)."
                    if I.actual() == "en" else
                    f"Altura de soporte sugerida: {fmt(sug)} mm (holgura inferior + cara inferior del PCB).")
            except Exception:
                self.lbl_sug.configure(text="")
            if self._step_pendiente(c):
                self.lbl_dims.configure(text="")
                self.lbl_val.configure(text="STEP sin medir con la orientación actual. Se medirá al "
                                       "calcular la caja o pulsando «Medir STEP».", style="Warn.TLabel")
                self._estado_vista()
                return
            try:
                cfg = M.config_from(c)
            except (ValueError, KeyError, TypeError) as ex:
                self.lbl_dims.configure(text="")
                self.lbl_val.configure(text="✗ " + str(ex), style="Bad.TLabel")
                self._estado_vista()
                return
            d = M.dimensions(cfg)
            z0 = cfg["floor"] + cfg["bottom_gap"]
            en = I.actual() == "en"
            self.lbl_dims.configure(text=(
                f"{T('Interior'):<9} {fmt(d['inner_x']):>7} × {fmt(d['inner_y']):>7} × {fmt(d['inner_z']):>7} mm\n"
                f"{T('Exterior'):<9} {fmt(d['outer_x']):>7} × {fmt(d['outer_y']):>7} × {fmt(d['outer_z']):>7} mm\n"
                + (f"Base {fmt(d['seam'])} mm high · lid {fmt(d['outer_z'] - d['seam'])} mm\n"
                   f"Board: z = {fmt(z0)} … {fmt(z0 + cfg['board_z'])} mm (from the outer bottom)" if en else
                   f"Base {fmt(d['seam'])} mm de alto · tapa {fmt(d['outer_z'] - d['seam'])} mm\n"
                   f"Placa: z = {fmt(z0)} … {fmt(z0 + cfg['board_z'])} mm (desde la base exterior)")))
            avisos = M.warnings(cfg)
            if avisos:
                self.lbl_val.configure(text="⚠ " + "\n⚠ ".join(avisos) + "\n" + T("Configuración válida."),
                                       style="Warn.TLabel")
            else:
                self.lbl_val.configure(text=T("✓ Configuración válida (las colisiones reales se comprueban "
                                         "al calcular en FreeCAD)."), style="Ok.TLabel")
            self._pista_puerto(cfg, d)
            self._pista_tornillo(cfg)
            self._programar_auto(cfg)
            self._estado_vista()

        def _pista_puerto(self, cfg=None, d=None):
            """Rango válido de u y v para la cara elegida en el formulario de aberturas."""
            try:
                cfg = cfg or self._validar()
                d = d or M.dimensions(cfg)
            except Exception:
                self.lbl_pista_p.configure(text="")
                return
            lado = LADO_KEY.get(self.pv["side"].get(), "frente")
            en = I.actual() == "en"
            if lado == "tapa":
                txt = ((f"Lid: the opening must stay inside the inner area, "
                        f"u ∈ ±{fmt(d['inner_x'] / 2)} (X), v ∈ ±{fmt(d['inner_y'] / 2)} (Y).") if en else
                       (f"Tapa: la abertura debe quedar dentro del área interior, "
                        f"u ∈ ±{fmt(d['inner_x'] / 2)} (X), v ∈ ±{fmt(d['inner_y'] / 2)} (Y)."))
            else:
                ancho = d["outer_y"] if lado in ("izquierda", "derecha") else d["outer_x"]
                txt = ((f"{T(LADO_TXT[lado])}: u ∈ ±{fmt(ancho / 2)}; v between {fmt(cfg['floor'])} and "
                        f"{fmt(d['outer_z'])}. Base/lid split at z = {fmt(d['seam'])}.") if en else
                       (f"{LADO_TXT[lado]}: u ∈ ±{fmt(ancho / 2)}; v entre {fmt(cfg['floor'])} y "
                        f"{fmt(d['outer_z'])}. Costura base/tapa en z = {fmt(d['seam'])}."))
            self.lbl_pista_p.configure(text=txt)

        def _estado_vista(self):
            """Indica si el visor muestra la caja de la configuración actual."""
            if self.vista_clave is None:
                self.lbl_vista.configure(text="El visor no muestra una caja calculada con esta configuración."
                                         if self._visor_vivo() else "", style="Muted.TLabel")
                return
            try:
                actual = self._clave_vista(self._validar())
            except Exception:
                actual = None
            if actual != self.vista_clave:
                self.lbl_vista.configure(text=T("⚠ El visor muestra una caja anterior: pulsa «Calcular caja "
                                           "en FreeCAD»."), style="Warn.TLabel")
            else:
                self.lbl_vista.configure(text=T("✓ El visor muestra los sólidos exactos de esta configuración."),
                                         style="Ok.TLabel")

        def _cargar_config(self, c):
            """Rellena todos los campos con una configuración (proyecto abierto o valores por defecto)."""
            self._cargando = True
            try:
                for k in CAMPOS_NUM:
                    self.v[k].set(fmt(c.get(k, 0)))
                self.v["name"].set(c.get("name", "Caja_circuito"))
                self.v["step_path"].set(c.get("step_path", ""))
                self.v["source"].set("step" if c.get("step_path") else "manual")
                self.v["up_axis"].set(c.get("up_axis", "Z"))
                self.v["board_fit"].set(c.get("board_fit", "pcb"))
                self.v["closure"].set(c.get("closure", "friccion"))
                self.v["view"].set(c.get("view", "separada"))
                self.var_stl.set(bool(c.get("export_stl", True)))
                self.var_step.set(bool(c.get("export_step", False)))
                self.v["board_rest"].set(c.get("board_rest", "esquinas"))
                self.v["screw_size"].set(c.get("screw_size", "M3"))
                self.v["screw_head"].set(c.get("screw_head", "cilindrica"))
                self.var_pisadores.set(bool(c.get("hold_down", False)))
                self.supports = [dict(s) for s in c.get("supports", [])]
                self.ports = [dict(p) for p in c.get("ports", [])]
                self._refrescar_tablas()
            finally:
                self._cargando = False
            self.medida, self.medida_clave = None, None
            if c.get("step_path"):
                # El STEP puede haber cambiado desde que se guardó el proyecto.
                self.lbl_medida.configure(text="Medidas guardadas de referencia. El STEP se volverá "
                                          "a medir antes de calcular o generar la caja.")
            else:
                self.lbl_medida.configure(text="")
            self.vista_clave = None
            self.lbl_vista.configure(text="")
            self._mostrar_avisos([])
            self._validar_en_vivo()

        def _crudo_sin_medida(self):
            """Datos mínimos de la placa STEP (ruta, eje y giro)."""
            return {"step_path": self.v["step_path"].get().strip(), "up_axis": self.v["up_axis"].get(),
                    "rotation": self.v["rotation"].get()}

        # ------------------------------------------------------------ tablas
        def _refrescar_tablas(self):
            """Vuelve a dibujar las tablas de aberturas y soportes."""
            self.tv_ports.delete(*self.tv_ports.get_children())
            for i, p in enumerate(self.ports):
                forma = T(FORMA_TXT.get(p.get("shape", "rectangular"), "Rectangular"))
                if p.get("to_seam"):
                    forma += " ↑"
                self.tv_ports.insert("", "end", iid=str(i), tags=("par",) if i % 2 else (), values=(
                    T(LADO_TXT.get(p["side"], p["side"])), forma, fmt(p["u"]), fmt(p["v"]),
                    fmt(p["width"]), fmt(p["height"])))
            self.tv_supports.delete(*self.tv_supports.get_children())
            for i, s in enumerate(self.supports):
                self.tv_supports.insert("", "end", iid=str(i), tags=("par",) if i % 2 else (), values=tuple(
                    fmt(s[k]) for k in ("x", "y", "height", "diameter", "hole")))

        def _tabla_de(self, tipo):
            """(tabla, lista, variables del formulario) de las aberturas o de los soportes."""
            return ((self.tv_ports, self.ports, self.pv) if tipo == "ports"
                    else (self.tv_supports, self.supports, self.sv))

        def _sel_tabla(self, tipo):
            """Al seleccionar una fila, copia sus valores al formulario."""
            tv, lista, vars_ = self._tabla_de(tipo)
            sel = tv.selection()
            if not sel:
                return
            item = lista[int(sel[0])]
            self._llenando_form = True
            try:
                for k, var in vars_.items():
                    valor = item.get(k, M.PORT_DEFAULTS.get(k) if tipo == "ports" else None)
                    if k == "side":
                        var.set(T(LADO_TXT.get(valor, valor)))
                    elif k == "shape":
                        var.set(T(FORMA_TXT.get(valor, FORMAS[0][0])))
                    elif k == "to_seam":
                        var.set(bool(valor))
                    else:
                        var.set(fmt(valor))
            finally:
                self._llenando_form = False
            if tipo == "ports":
                self._pista_puerto()

        def _leer_form(self, tipo):
            """Fila de la tabla según el formulario. Lanza ValueError con un mensaje."""
            _, _, vars_ = self._tabla_de(tipo)
            item = {}
            try:
                for k, var in vars_.items():
                    if k == "side":
                        item[k] = LADO_KEY[var.get()]
                    elif k == "shape":
                        item[k] = FORMA_KEY[var.get()]
                    elif k == "to_seam":
                        item[k] = bool(var.get())
                    else:
                        item[k] = M.number(var.get())
            except (ValueError, KeyError):
                raise ValueError("Revisa los números (se acepta coma decimal).") from None
            if tipo == "supports" and not item["hole"] < item["diameter"]:
                raise ValueError("El agujero debe ser menor que el diámetro.")
            if tipo == "ports" and item["shape"] == "circular":
                item["height"] = item["width"]
            return item

        def _editar_en_vivo(self, tipo):
            """Con una fila seleccionada, cada cambio válido del formulario se aplica a ella
            (y, con la actualización automática, al visor)."""
            if self._llenando_form or self._cargando:
                return
            tv, lista, _ = self._tabla_de(tipo)
            sel = tv.selection()
            if not sel:
                return
            try:
                item = self._leer_form(tipo)
            except ValueError:
                return              # número a medio escribir: se espera al siguiente cambio
            idx = int(sel[0])
            if idx < len(lista) and lista[idx] != item:
                lista[idx] = item
                self._refrescar_tablas()
                tv.selection_set(str(idx))
                self._cambio()

        def _editar_tabla(self, tipo, accion):
            """Agrega, actualiza, elimina o deselecciona una fila según `accion`."""
            tv, lista, _ = self._tabla_de(tipo)
            sel = tv.selection()
            idx = int(sel[0]) if sel else None
            if accion == "none":
                tv.selection_remove(*tv.selection())
                return
            if accion in ("upd", "del") and idx is None:
                messagebox.showinfo("Selecciona una fila", "Primero selecciona una fila de la tabla.",
                                    parent=self)
                return
            if accion == "del":
                del lista[idx]
            else:
                try:
                    item = self._leer_form(tipo)
                except ValueError as ex:
                    messagebox.showerror("Dato no válido", str(ex), parent=self)
                    return
                if accion == "add":
                    lista.append(item)
                    idx = len(lista) - 1
                else:
                    lista[idx] = item
            self._refrescar_tablas()
            if accion != "del" and idx is not None:
                tv.selection_set(str(idx))
            self._cambio()

        def _altura_sugerida(self):
            """Escribe la altura de soporte que deja el PCB apoyado en él."""
            try:
                c = self._crudo()
                self.sv["height"].set(fmt(M.suggested_support_height(M.config_from(dict(c, step_path="")))))
            except Exception as ex:
                messagebox.showerror("No se puede sugerir", str(ex), parent=self)

        # ------------------------------------------------------------ FreeCAD
        def _actualizar_fc(self):
            """Muestra qué FreeCAD se está usando."""
            if self.python_fc:
                carpeta = Path(self.python_fc).parent.parent.name or str(self.python_fc)
                self.lbl_fc.configure(text=f"✓ {carpeta}", style="Help.TLabel")
            else:
                self.lbl_fc.configure(text="FreeCAD no encontrado: indica su python.exe", style="Bad.TLabel")

        def elegir_freecad(self):
            """Permite elegir a mano el python.exe (o freecad.exe) de FreeCAD."""
            ruta = filedialog.askopenfilename(
                parent=self, title="Python de FreeCAD (bin\\python.exe) o freecad.exe",
                filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")],
                initialdir=r"C:\Program Files")
            if not ruta:
                return
            p = python_desde(ruta)
            if not p:
                messagebox.showerror("FreeCAD", "No encuentro python.exe junto a ese archivo. Elige el "
                                     "python.exe de la carpeta bin de FreeCAD.", parent=self)
                return
            self.python_fc = p
            self.ajustes["freecad_python"] = str(p)
            guardar_ajustes(self.ajustes)
            self._actualizar_fc()

        def _lanzar(self, accion, config, folder=None, etiqueta_=""):
            """Lanza una acción del worker (visor cerrado), bloqueando la interfaz."""
            if not self._libre():
                self.lbl_estado.configure(text="Espera a que termine la operación en curso.",
                                          style="Warn.TLabel")
                return False
            if not self.python_fc:
                messagebox.showerror("FreeCAD no encontrado",
                                     "Indica el python.exe de FreeCAD con el botón «Python de FreeCAD…».",
                                     parent=self)
                self.cadena = []
                return False
            if not WORKER.is_file():
                messagebox.showerror("Falta archivo", f"No encuentro {WORKER.name} junto a la aplicación.",
                                     parent=self)
                self.cadena = []
                return False
            self._ocupado(True, etiqueta_)
            self.trab.lanzar(self.python_fc, accion, config, folder, etiqueta_)
            return True

        def _ocupado(self, si, texto=""):
            """Bloquea o desbloquea la interfaz durante una operación larga y muestra el progreso."""
            if si and not self._ui_bloqueada:
                self._estados_controles = []
                def bloquear(padre):
                    for w in padre.winfo_children():
                        if isinstance(w, (ttk.Entry, ttk.Button, ttk.Combobox,
                                          ttk.Radiobutton, ttk.Checkbutton, ttk.Treeview, ttk.Scale)) and \
                                w not in (self.btn_cancelar, self.btn_visor_reiniciar):
                            self._estados_controles.append((w, w.state()))
                            w.state(["disabled"])
                        bloquear(w)
                bloquear(self)
            elif not si and self._ui_bloqueada:
                for w, estados in self._estados_controles:
                    if w.winfo_exists():
                        w.state(["!disabled"])
                        w.state(estados)
                self._estados_controles = []
            self._ui_bloqueada = si
            self.btn_cancelar.configure(state="normal" if si else "disabled")
            if si:
                self.prog.pack(side="right", before=self.btn_cancelar)
                self.prog.start(12)
                self.lbl_estado.configure(text=texto + "…", style="TLabel")
            else:
                self.prog.stop()
                self.prog.pack_forget()

        def cancelar(self):
            """Deja de esperar la operación en curso."""
            self.cadena = []
            self.trab.cancelar()
            self.tvisor.cancelar()

        def _procesar_cola(self):
            """Bucle (80 ms) que aplica en Tk los resultados que llegan de los hilos."""
            try:
                while True:
                    tipo, accion, dato, dur, texto = self.cola.get_nowait()
                    self._ocupado(False)
                    if tipo == "error":
                        self.cadena = []
                        self.lbl_estado.configure(text=f"{texto}: error ({dur:.1f} s).", style="Bad.TLabel")
                        detalle = getattr(dato, "detalle", "")
                        if detalle:
                            print(detalle, file=sys.stderr)
                        tipo_err = getattr(dato, "tipo", "")
                        if accion == "v_vista":
                            self.lbl_estado.configure(text="Vista no aplicada: " + str(dato), style="Warn.TLabel")
                        elif tipo_err == "cancelada" or str(dato) == "Operación cancelada.":
                            self.lbl_estado.configure(
                                text="Se dejó de esperar. FreeCAD puede seguir calculando; si no responde, "
                                     "usa «Reiniciar visor».", style="Warn.TLabel")
                        elif self._reiniciando and tipo_err in ("cerrada", "sin_sesion", "arranque"):
                            pass
                        elif accion == "v_auto":
                            # Sin diálogos: el usuario sigue escribiendo; el error queda a la vista.
                            self.lbl_vista.configure(text="✗ No se pudo actualizar el visor: " + str(dato),
                                                     style="Bad.TLabel")
                        elif tipo_err == "validacion":
                            messagebox.showerror("Configuración no válida", str(dato), parent=self)
                        else:
                            messagebox.showerror("FreeCAD", str(dato), parent=self)
                        self._actualizar_visor()
                        continue
                    self.lbl_estado.configure(text=f"{texto}: listo en {dur:.1f} s.", style="Ok.TLabel")
                    getattr(self, "_fin_" + accion)(dato)
                    if accion.startswith("v_"):
                        self._actualizar_visor()
                    if self.cadena and not self.trab.ocupado and not self.tvisor.ocupado:
                        paso = self.cadena.pop(0)
                        paso()
            except queue.Empty:
                pass
            except Exception as ex:
                self.cadena = []
                self._ocupado(False)
                self.lbl_estado.configure(text="No se pudo aplicar el resultado de FreeCAD.", style="Bad.TLabel")
                messagebox.showerror("Resultado de FreeCAD", str(ex), parent=self)
            finally:
                self.after(80, self._procesar_cola)

        # ---------------------------------------------------- medición STEP
        def elegir_step(self):
            """Elige el archivo STEP y lo mide."""
            if self._ui_bloqueada:
                return
            ruta = filedialog.askopenfilename(parent=self, title="Placa en formato STEP",
                                              filetypes=[("STEP", "*.step *.stp *.STEP *.STP"),
                                                         ("Todos", "*.*")])
            if ruta:
                self.v["step_path"].set(str(Path(ruta)))
                self.v["source"].set("step")
                self.medir()

        def medir(self, despues=None):
            """Mide el STEP: con el visor (queda en memoria y se muestra) o, sin él, con el worker."""
            if self._ui_bloqueada:
                return
            c = self._crudo()
            if not c["step_path"]:
                messagebox.showinfo("STEP", "Elige primero el modo STEP y un archivo .step/.stp.", parent=self)
                return
            if not Path(c["step_path"]).is_file():
                messagebox.showerror("STEP", "No existe el archivo:\n" + c["step_path"], parent=self)
                return
            try:
                M.number(c["rotation"])
            except ValueError:
                messagebox.showerror("Giro", "El giro debe ser un número en grados.", parent=self)
                return
            self.cadena = [despues] if despues else []
            self._clave_en_curso = self._clave_step(c)
            if self._exe_freecad() is not None:
                datos = {"config": {k: c[k] for k in ("step_path", "up_axis", "rotation", "board_fit",
                                                      "board_x", "board_y", "board_z")},
                         **self._datos_vista()}

                def pasos(s):
                    s.iniciar()
                    return s.solicitar("cargar_placa", datos, plazo=900)
                self._lanzar_visor("v_cargar", pasos, "Leyendo la placa en FreeCAD")
                return
            # Sin visor: worker. Para medir solo hace falta la placa: se envían las claves mínimas.
            pet = {k: c[k] for k in ("step_path", "up_axis", "board_fit")}
            pet["rotation"] = M.number(c["rotation"])
            pet.update({"board_x": 1.0, "board_y": 1.0, "board_z": 1.0})
            self._clave_en_curso = self._clave_step(c)
            self._lanzar("inspect", pet, etiqueta_="Midiendo STEP con FreeCAD")

        def _fin_inspect(self, r):
            """Aplica una medición del STEP. Devuelve False si el STEP cambió mientras se medía."""
            if self._clave_en_curso != self._clave_step(self._crudo()):
                self.cadena = []
                self.lbl_medida.configure(text="El STEP cambió durante la lectura. Vuelve a medirlo.")
                return False
            nueva = self.medida_clave != self._clave_en_curso
            if not nueva and self.medida and r.get("solids") is None:
                r = dict(r, solids=self.medida.get("solids"))
            self.medida = r
            self.medida_clave = self._clave_en_curso
            self._poner_medidas(self._medida_para(self.v["board_fit"].get()))
            sizes = r.get("sizes") or {"total": r["size"], "pcb": None}
            tx, ty, tz = sizes["total"]
            lineas = [f"Medido con FreeCAD ({r.get('solids') or '?'} sólidos). Con todos los componentes: "
                      f"{tx:.2f} × {ty:.2f} × {tz:.2f} mm."]
            if sizes.get("pcb"):
                px, py, pz = sizes["pcb"]
                lineas.append(f"PCB sin componentes: {px:.2f} × {py:.2f} mm, espesor {pz:.2f} mm, "
                              f"cara inferior a {r['pcb_z']:.2f} mm del punto más bajo.")
                if nueva and r.get("pcb_z") is not None:
                    # Solo al medir una placa u orientación nueva: no pisa lo que escribió el usuario.
                    cargando, self._cargando = self._cargando, True
                    self.v["pcb_offset"].set(fmt(round(r["pcb_z"], 3)))
                    self._cargando = cargando
            else:
                lineas.append("No se identificó el PCB (ningún sólido plano grande): «Solo el PCB» "
                              "usará la envolvente con componentes.")
            if "colores" in r:
                lineas.append("Colores del STEP (KiCad): " + ("se muestran en el visor." if r["colores"]
                              else "el archivo no trae colores; la placa se muestra en verde."))
            lineas += list(r.get("warnings") or [])
            self.lbl_medida.configure(text="\n".join(lineas))
            self.vista_clave = None
            self._cambio()
            return True

        def _fin_v_cargar(self, r):
            """Resultado de cargar la placa en el visor."""
            if self._fin_inspect(r):
                self._info_visor(r, "Placa cargada")
                self._mostrar_avisos(list(r.get("warnings", [])) + [
                    "Placa mostrada en FreeCAD con sus sólidos nativos. Calcula la caja para verla "
                    "alrededor de la placa."])

        # ---------------------------------------------------- vista previa
        def vista_previa(self, auto=False):
            """Calcula la caja en el visor (lo abre si hace falta). Con STEP, el puente mide la
            placa (desde su caché) y completa las medidas antes de validar. Con auto=True es la
            actualización automática: no bloquea la interfaz y no muestra diálogos."""
            if self._ui_bloqueada:
                return
            c = self._crudo()
            if c["step_path"]:
                if not Path(c["step_path"]).is_file():
                    if not auto:
                        messagebox.showerror("STEP", "No existe el archivo:\n" + c["step_path"], parent=self)
                    return
                self._clave_en_curso = self._clave_step(c)
            else:
                try:
                    M.config_from(c)
                except (ValueError, KeyError, TypeError) as ex:
                    if not auto:
                        messagebox.showerror("Configuración no válida", str(ex), parent=self)
                    return
                self._clave_en_curso = None
            datos = {"config": c, **self._datos_vista()}

            def pasos(s):
                s.iniciar()
                return s.solicitar("calcular", datos, plazo=900)
            if auto:
                self._lanzar_visor("v_auto", pasos, "Actualizando el visor", bloquear=False)
            else:
                self._lanzar_visor("v_calcular", pasos, "Calculando la caja en FreeCAD")

        def _fin_v_calcular(self, r):
            """Resultado de calcular la caja: medidas de la placa, avisos y estado de la vista."""
            cfg = r["config"]
            if cfg.get("step_path"):
                if self._clave_en_curso != self._clave_step(self._crudo()):
                    self.lbl_vista.configure(text="La placa u orientación cambió durante el cálculo. "
                                             "Vuelve a calcular.", style="Warn.TLabel")
                    return
                self._fin_inspect({"size": r["size"], "sizes": r.get("sizes"), "pcb_z": r.get("pcb_z"),
                                   "warnings": [], "solids": None})
            self.vista_clave = self._clave_vista(cfg)
            self._mostrar_avisos(self._avisos(r))
            self._info_visor(r, "Última actualización")
            self._estado_vista()

        def _cambio_auto(self):
            """Guarda la opción de actualización automática y la aplica."""
            self.ajustes["auto_visor"] = bool(self.var_auto.get())
            guardar_ajustes(self.ajustes)
            self._programar_auto()

        def _fin_v_auto(self, r):
            """Resultado de una actualización automática; reprograma si algo cambió mientras tanto."""
            self._fin_v_calcular(r)
            self._programar_auto()      # por si algo cambió mientras se calculaba

        # ------------------------------------------- actualización automática
        def _programar_auto(self, cfg=None):
            """Si el visor está abierto y la caja que muestra no corresponde a la configuración
            (válida) actual, programa un recálculo sin bloquear la interfaz."""
            if not self.var_auto.get() or not self._visor_vivo():
                return
            if cfg is None:
                try:
                    cfg = self._validar()
                except Exception:
                    return
            if self._clave_vista(cfg) == self.vista_clave:
                return
            if self._tarea_auto:
                self.after_cancel(self._tarea_auto)
            self._tarea_auto = self.after(AUTO_MS, self._auto_calcular)

        def _auto_calcular(self):
            """Lanza la actualización automática cuando no hay otra operación en curso."""
            self._tarea_auto = None
            if not self.var_auto.get() or not self._visor_vivo():
                return
            if not self._libre():
                self._tarea_auto = self.after(400, self._auto_calcular)
                return
            try:
                cfg = self._validar()
            except Exception:
                return
            if self._clave_vista(cfg) != self.vista_clave:
                self.vista_previa(auto=True)

        # ------------------------------------------------ aberturas automáticas
        def detectar_aberturas(self):
            """Pide al visor aberturas para los componentes que atraviesan las paredes."""
            if self._ui_bloqueada:
                return
            try:
                margen = M.number(self.var_det_margen.get(), "Juego alrededor")
                if not 0 <= margen <= 5:
                    raise ValueError("El juego alrededor debe estar entre 0 y 5 mm.")
            except ValueError as ex:
                messagebox.showerror("Detectar aberturas", str(ex), parent=self)
                return
            c = self._crudo()
            if not c["step_path"]:
                messagebox.showinfo("Detectar aberturas", "La detección necesita la placa en STEP: en "
                                    "modo manual no hay componentes que detectar.", parent=self)
                return
            if not Path(c["step_path"]).is_file():
                messagebox.showerror("STEP", "No existe el archivo:\n" + c["step_path"], parent=self)
                return
            datos = {"config": c, "margen": margen, "hasta_union": bool(self.var_det_union.get())}
            self._clave_en_curso = self._clave_step(c)

            def pasos(s):
                s.iniciar()
                return s.solicitar("detectar", datos, plazo=900)
            self._lanzar_visor("v_detectar", pasos, "Buscando componentes que atraviesan la caja")

        def _fin_v_detectar(self, r):
            """Agrega las aberturas detectadas y resume lo que se agregó."""
            if self._clave_en_curso != self._clave_step(self._crudo()):
                self.lbl_estado.configure(text="La placa cambió durante la detección. Vuelve a detectar.",
                                          style="Warn.TLabel")
                return
            nuevas = list(r.get("ports", []))
            notas = list(r.get("skipped", []))
            if not nuevas:
                texto = "Ningún componente atraviesa la caja: no hace falta agregar aberturas."
                if self.v["board_fit"].get() == "total":
                    texto += (" Con «Envolvente con todos los componentes» la caja ya los contiene; "
                              "usa «Solo el PCB» para que los conectores salgan por las paredes.")
                messagebox.showinfo("Detectar aberturas", "\n".join([texto] + notas), parent=self)
                return
            self.ports.extend(nuevas)
            self._refrescar_tablas()
            lineas = [f"Se agregaron {len(nuevas)} aberturas para {r.get('components', len(nuevas))} "
                      "componentes (al final de la tabla de Aberturas):"]
            for p in nuevas:
                lineas.append(f"  {LADO_TXT.get(p['side'], p['side'])}: {FORMA_TXT.get(p['shape'], p['shape'])}"
                              f" {fmt(p['width'])} × {fmt(p['height'])} mm en u = {fmt(p['u'])}, "
                              f"v = {fmt(p['v'])}" + (" (hasta la unión)" if p.get("to_seam") else ""))
            self._mostrar_avisos(lineas + notas)
            self._cambio()
            if not self.var_auto.get():
                self.lbl_vista.configure(text="⚠ Aberturas agregadas: pulsa «Calcular caja en FreeCAD» "
                                         "para verlas.", style="Warn.TLabel")

        # ------------------------------------------- aberturas en la tapa
        def elegir_componentes_tapa(self):
            """Pide al visor la lista de componentes de la placa para elegir sobre cuáles abrir
            la tapa."""
            if self._ui_bloqueada:
                return
            c = self._crudo()
            if not c["step_path"]:
                messagebox.showinfo("Aberturas en la tapa", "La lista de componentes necesita la placa "
                                    "en STEP.", parent=self)
                return
            if not Path(c["step_path"]).is_file():
                messagebox.showerror("STEP", "No existe el archivo:\n" + c["step_path"], parent=self)
                return
            self._clave_en_curso = self._clave_step(c)
            datos = {"config": c}

            def pasos(s):
                s.iniciar()
                return s.solicitar("componentes", datos, plazo=900)
            self._lanzar_visor("v_componentes", pasos, "Leyendo los componentes de la placa")

        def _fin_v_componentes(self, r):
            """Abre la lista de componentes para elegir aberturas en la tapa."""
            if self._clave_en_curso != self._clave_step(self._crudo()):
                self.lbl_estado.configure(text="La placa cambió mientras se leía. Vuelve a intentarlo.",
                                          style="Warn.TLabel")
                return
            comps = list(r.get("componentes", []))
            if not comps:
                messagebox.showinfo("Aberturas en la tapa", "La placa no tiene componentes aparte del PCB.",
                                    parent=self)
                return
            self.dialogo_componentes = DialogoComponentes(self, comps, bool(r.get("con_nombres")),
                                                          self._crear_aberturas_tapa)

        def _crear_aberturas_tapa(self, elegidos, margen):
            """Agrega aberturas de tapa sobre los componentes elegidos. Devuelve True si se
            agregó alguna (el diálogo se cierra)."""
            try:
                cfg = self._validar()
            except Exception as ex:
                messagebox.showerror("Aberturas en la tapa", "Corrige primero la configuración:\n" + str(ex),
                                     parent=self)
                return False
            nuevas, notas = M.lid_ports(elegidos, M.dimensions(cfg), margen, self.ports)
            if not nuevas:
                messagebox.showinfo("Aberturas en la tapa", "\n".join(
                    ["No hay aberturas nuevas que agregar."] + notas), parent=self)
                return False
            self.ports.extend(nuevas)
            self._refrescar_tablas()
            lineas = [f"Se agregaron {len(nuevas)} aberturas en la tapa sobre {len(elegidos)} componentes:"]
            for p in nuevas:
                lineas.append(f"  {FORMA_TXT.get(p['shape'], p['shape'])} {fmt(p['width'])} × "
                              f"{fmt(p['height'])} mm en X = {fmt(p['u'])}, Y = {fmt(p['v'])}")
            self._mostrar_avisos(lineas + notas)
            self._cambio()
            if not self.var_auto.get():
                self.lbl_vista.configure(text="⚠ Aberturas agregadas: pulsa «Calcular caja en FreeCAD» "
                                         "para verlas.", style="Warn.TLabel")
            return True

        def _union_aberturas(self):
            """Pone la unión a la altura (mediana) de las aberturas laterales."""
            vs = sorted(p["v"] for p in self.ports if p["side"] != "tapa")
            if not vs:
                messagebox.showinfo("Altura de la unión", "No hay aberturas laterales. Agrégalas (o "
                                    "detéctalas) primero.", parent=self)
                return
            self.v["split_height"].set(fmt(round(vs[len(vs)//2], 2)))

        def _avisos(self, r):
            """Avisos del cálculo y resumen del cierre para el panel de avisos."""
            av = list(r.get("warnings", []))
            av.append(f"Lengüetas: {r.get('tongues', 0)} · interferencia de nervios en cierre: "
                      f"{r.get('fit_interference', 0):.3f} mm³ · colisión placa–caja: "
                      f"{r.get('collision', 0):.3f} mm³.")
            av.append("El ajuste del cierre requiere una impresión de prueba.")
            return av

        def _mostrar_avisos(self, lineas):
            """Avisos (⚠, amarillo), errores (✗, rojo) e información (•, gris)."""
            self.warn_box.configure(state="normal")
            self.warn_box.delete("1.0", "end")
            for linea in lineas:
                texto = str(linea)
                if texto.startswith("  "):
                    tag, pref = "detalle", ""
                elif texto.startswith("✗"):
                    tag, pref = "error", ""
                elif texto.startswith(INFO_AVISOS):
                    tag, pref = "info", "•  "
                else:
                    tag, pref = "aviso", "⚠  "
                self.warn_box.insert("end", pref + texto.strip() + "\n", tag)
            self.warn_box.configure(state="disabled")

        # ---------------------------------------------------- exportación
        def elegir_salida(self):
            """Elige la carpeta donde se crean las generaciones."""
            d = filedialog.askdirectory(parent=self, title="Carpeta donde crear las generaciones",
                                        initialdir=self.var_salida.get() or str(AQUI))
            if d:
                self.var_salida.set(str(Path(d)))

        def generar(self):
            """Genera FCStd/STL/STEP, proyecto y macro en una carpeta nueva (con el visor o el worker)."""
            if self._ui_bloqueada:
                return
            c = self._crudo()
            if self._step_pendiente(c):
                self.medir(despues=self.generar)
                return
            try:
                cfg = M.config_from(c)
            except (ValueError, KeyError, TypeError) as ex:
                messagebox.showerror("Configuración no válida", str(ex), parent=self)
                return
            padre = Path(self.var_salida.get().strip() or (AQUI / "cajas_generadas"))
            try:
                padre.mkdir(parents=True, exist_ok=True)
                carpeta = M.unique_output_dir(padre, cfg["name"])
                nombre = M.safe_name(cfg["name"])
                M.save_project(carpeta / (nombre + ".cajas.json"), cfg)
                M.write_macro(carpeta / (nombre + ".FCMacro"), cfg)
            except Exception as ex:
                messagebox.showerror("No se pudo preparar la carpeta", str(ex), parent=self)
                return
            self.ajustes["salida"] = str(padre)
            guardar_ajustes(self.ajustes)
            self._carpeta_en_curso = carpeta
            self._cfg_en_curso = cfg
            if self._visor_vivo():
                # Reutiliza la placa en memoria y, si coincide, la caja ya calculada.
                datos = {"config": cfg, "carpeta": str(carpeta)}
                self._lanzar_visor("v_exportar", lambda s: s.solicitar("exportar", datos, plazo=900),
                                   "Generando modelos en el visor FreeCAD")
            else:
                self._lanzar("export", cfg, folder=carpeta, etiqueta_="Generando modelos con FreeCAD")

        def _fin_export(self, r):
            """Resultado de la generación con el worker."""
            self.ultima_salida = self._carpeta_en_curso
            self.btn_abrir_salida.configure(state="normal")
            archivos = [Path(f).name for f in r.get("files", [])]
            self._mostrar_avisos(self._avisos(r))
            messagebox.showinfo("Generación terminada",
                                f"Carpeta nueva:\n{self.ultima_salida}\n\nArchivos de FreeCAD: "
                                + ", ".join(archivos) + "\nAdemás: proyecto .cajas.json y macro .FCMacro.",
                                parent=self)

        def _fin_v_exportar(self, r):
            """Resultado de la generación con el visor."""
            self._info_visor(r, "Exportación")
            self._fin_export(r)

        def _abrir_ultima(self):
            """Abre en el explorador la última carpeta generada."""
            if self.ultima_salida and Path(self.ultima_salida).is_dir():
                os.startfile(self.ultima_salida)  # noqa: S606 - abre el explorador

        def exportar_macro(self):
            """Guarda una macro .FCMacro autónoma con el constructor y la configuración."""
            if self._ui_bloqueada:
                return
            c = self._crudo()
            if self._step_pendiente(c):
                self.medir(despues=self.exportar_macro)
                return
            try:
                cfg = M.config_from(c)
            except (ValueError, KeyError, TypeError) as ex:
                messagebox.showerror("Configuración no válida", str(ex), parent=self)
                return
            ruta = filedialog.asksaveasfilename(
                parent=self, title="Guardar macro de FreeCAD", defaultextension=".FCMacro",
                initialfile=M.safe_name(cfg["name"]) + ".FCMacro",
                filetypes=[("Macro de FreeCAD", "*.FCMacro"), ("Python", "*.py")])
            if not ruta:
                return
            try:
                # El diálogo ya pidió confirmación si el archivo existía.
                M.write_macro(ruta, cfg, overwrite=True)
            except Exception as ex:
                messagebox.showerror("Macro", str(ex), parent=self)
                return
            self.lbl_estado.configure(text=f"Macro guardada: {ruta}", style="Ok.TLabel")

        # ---------------------------------------------------- proyectos
        def _actualizar_titulo(self):
            """Título de la ventana: proyecto y marca de cambios sin guardar."""
            nombre = Path(self.proyecto).name if self.proyecto else T("Proyecto sin guardar")
            self.title(f"{'*' if self.modificado else ''}{nombre} — {T('Cajas para circuitos')}")

        def _descartar_cambios(self):
            """Pregunta si guardar los cambios. Devuelve False si el usuario cancela."""
            if not self.modificado:
                return True
            r = messagebox.askyesnocancel("Cambios sin guardar", "¿Guardar los cambios del proyecto?",
                                          parent=self)
            if r is None:
                return False
            return self.guardar() if r else True

        def nuevo(self):
            """Proyecto nuevo con los valores por defecto."""
            if self._ui_bloqueada:
                return
            if not self._descartar_cambios():
                return
            self.proyecto = None
            self._cargar_config(M.config_from({}))
            self.modificado = False
            self._actualizar_titulo()

        def abrir(self):
            """Abre un proyecto .cajas.json."""
            if self._ui_bloqueada:
                return
            if not self._descartar_cambios():
                return
            ruta = filedialog.askopenfilename(parent=self, title="Abrir proyecto",
                                              filetypes=[("Proyecto de cajas", "*.json"), ("Todos", "*.*")])
            if not ruta:
                return
            try:
                cfg = M.load_project(ruta, validate_config=False)
            except Exception as ex:
                messagebox.showerror("No se pudo abrir", str(ex), parent=self)
                return
            self.proyecto = ruta
            self._cargar_config(cfg)
            self.modificado = False
            self._actualizar_titulo()
            if cfg.get("step_path") and not Path(cfg["step_path"]).is_file():
                messagebox.showwarning("STEP no encontrado",
                                       "El proyecto apunta a un STEP que no existe:\n" + cfg["step_path"],
                                       parent=self)

        def guardar(self):
            """Guarda el proyecto (pide ruta si es nuevo)."""
            if not self.proyecto:
                return self.guardar_como()
            return self._guardar_en(self.proyecto)

        def guardar_como(self):
            """Guarda el proyecto con otro nombre."""
            try:
                nombre = M.safe_name(self.v["name"].get() or "Caja_circuito")
            except Exception:
                nombre = "Caja_circuito"
            ruta = filedialog.asksaveasfilename(parent=self, title="Guardar proyecto",
                                                defaultextension=".json", initialfile=nombre + ".cajas.json",
                                                filetypes=[("Proyecto de cajas", "*.json")])
            if not ruta:
                return False
            return self._guardar_en(ruta)

        def _guardar_en(self, ruta):
            """Valida y guarda el proyecto en `ruta`."""
            try:
                cfg = self._validar()
                M.save_project(ruta, cfg)
            except Exception as ex:
                messagebox.showerror("No se pudo guardar",
                                     f"{ex}\n\nCorrige la configuración y vuelve a guardar.", parent=self)
                return False
            self.proyecto = ruta
            self.modificado = False
            self._actualizar_titulo()
            self.lbl_estado.configure(text=f"Proyecto guardado: {ruta}", style="Ok.TLabel")
            return True

        def ayuda(self):
            """Resumen de uso."""
            messagebox.showinfo("Ayuda", (
                "1. Placa: medidas manuales o un STEP. Con STEP elige el eje vertical y el giro; "
                "FreeCAD mide la envolvente de todos los componentes.\n"
                "2. Caja y Cierre: holguras, paredes y encaje sin tornillos.\n"
                "3. Aberturas y Soportes: se definen a mano (no hay detección automática).\n"
                "4. «Calcular caja en FreeCAD» abre el visor (una ventana de FreeCAD administrada por "
                "esta aplicación), comprueba colisiones y muestra los sólidos reales. La placa queda en "
                "memoria: los cambios siguientes solo regeneran base y tapa.\n"
                "5. Exportar: carpeta nueva con FCStd, STL/STEP, proyecto y macro.\n\n"
                "Coordenadas: X/Y desde el centro de la caja; Z desde la base exterior. "
                "Se acepta coma decimal. Consulta README.md."), parent=self)

        def _cerrar(self):
            """Al cerrar: pregunta por los cambios, cierra el visor y guarda los ajustes."""
            if not self._descartar_cambios():
                return
            self.trab.cancelar()
            if self.sesion is not None:
                if self.tvisor.ocupado:
                    self.tvisor.cancelar()
                    self.sesion.matar_sin_esperar()
                else:
                    try:
                        self.sesion.detener(espera=5)
                    except Exception:
                        self.sesion.matar_sin_esperar()
            self.destroy()

    return App()


def main():
    """Punto de entrada: abre la aplicación."""
    crear_app().mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror("Cajas para circuitos", traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)

"""Puente de «Cajas para circuitos» que se ejecuta DENTRO de FreeCAD (con GUI).

No se ejecuta a mano. Lo lanza la aplicación (cajas_sesion_freecad.py):

    freecad.exe --user-cfg <sesión>/user.cfg cajas_puente_freecad.py

La aplicación pasa el directorio de sesión, el token, la carpeta de la
aplicación y su propio PID en variables de entorno. Si faltan, el puente no
hace nada. Así, si este archivo se abre a mano en un FreeCAD del usuario, no
se toca ninguno de sus documentos.

El puente atiende peticiones JSON (cajas_protocolo.py) con un QTimer en el
hilo principal de Qt. Mantiene en memoria la placa STEP (BoardCache) y trabaja
solo en su propio documento «CajasVistaPrevia». Mientras OCC calcula, el hilo
de Qt está ocupado: no atiende peticiones ni puede cancelar ese cálculo. La
aplicación lo sabe por el latido («ocupado» y el id de la petición).
"""
import os
import sys
import time
import traceback

DIR_SESION = os.environ.get("CAJAS_PUENTE_DIR", "")
TOKEN = os.environ.get("CAJAS_PUENTE_TOKEN", "")
DIR_APP = os.environ.get("CAJAS_PUENTE_APP", "")
PID_PADRE = os.environ.get("CAJAS_PUENTE_PADRE", "")

NOMBRE_DOC = "CajasVistaPrevia"
INTERVALO_MS = 100
INTERVALO_LATIDO = 0.5
INTERVALO_PADRE = 2.0


def _registrar(texto):
    """Añade una línea con hora al registro puente.log de la sesión."""
    try:
        with open(os.path.join(DIR_SESION, "puente.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + texto.rstrip() + "\n")
    except OSError:
        pass


class Puente:
    """Atiende las peticiones de la aplicación dentro de FreeCAD con un QTimer (sin bloquear la GUI)."""
    def __init__(self):
        """Importa FreeCAD y los módulos de la aplicación, crea la caché y arranca el temporizador."""
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtCore
        import cajas_protocolo as P
        import cajas_modelo as M
        import cajas_freecad as CF

        self.App, self.Gui, self.QtCore = App, Gui, QtCore
        self.P, self.M, self.CF = P, M, CF
        self.cache = CF.BoardCache(colors=True)   # con GUI: conserva los colores del STEP
        self.estado_doc = {}          # estado de sync_document (incluye board_key)
        self.result = None            # último resultado de build
        self.cfg_clave = None         # JSON de la configuración del último build
        self.vista = {"vista": "cerrada", "visible": {"base": True, "lid": True, "board": True,
                                                      "hardware": True},
                      "transparencia": {"base": 0, "lid": 0},
                      "colores": {"step": True}}   # #RRGGBB por pieza; step: colores del STEP
        self.colores_placa = None     # colores por cara de la placa mostrada (del STEP)
        self.ocupado = False
        self.id_actual = None
        self.inicio_actual = None
        self.deteniendo = False
        self.ultimo_latido = 0.0
        self.ultima_revision_padre = time.time()
        self.atendidas = 0
        self.inicio = time.time()
        P.preparar_directorio(DIR_SESION)
        self.manejadores = {
            "estado": self.a_estado, "cargar_placa": self.a_cargar_placa,
            "calcular": self.a_calcular, "vista": self.a_vista, "exportar": self.a_exportar,
            "captura": self.a_captura, "detectar": self.a_detectar,
            "componentes": self.a_componentes, "detener": self.a_detener,
        }
        assert set(self.manejadores) == set(P.ACCIONES)
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(INTERVALO_MS)
        self.latido(forzar=True)
        _registrar(f"Puente iniciado (pid {os.getpid()}).")

    # ------------------------------------------------------------ latido
    def latido(self, forzar=False, estado=None):
        """Publica latido.json (estado, pid, petición en curso) cada medio segundo o al forzarlo."""
        ahora = time.time()
        if not forzar and ahora - self.ultimo_latido < INTERVALO_LATIDO:
            return
        self.ultimo_latido = ahora
        datos = {"protocolo": self.P.PROTOCOLO, "token": TOKEN, "pid": os.getpid(), "ts": ahora,
                 "estado": estado or ("deteniendo" if self.deteniendo else
                                      "ocupado" if self.ocupado else "listo"),
                 "id_actual": self.id_actual, "inicio_actual": self.inicio_actual,
                 "atendidas": self.atendidas}
        try:
            self.P.escribir_json_atomico(os.path.join(DIR_SESION, self.P.LATIDO), datos)
        except OSError as ex:
            _registrar("No se pudo escribir el latido: " + str(ex))

    # ------------------------------------------------------------ bucle
    def tick(self):
        """Cada 100 ms: comprueba que la aplicación siga viva, late y atiende una petición."""
        if self.ocupado or self.deteniendo:
            return
        try:
            ahora = time.time()
            if PID_PADRE and ahora - self.ultima_revision_padre > INTERVALO_PADRE:
                self.ultima_revision_padre = ahora
                if not self.P.proceso_vivo(int(PID_PADRE)):
                    _registrar("La aplicación se cerró: el puente se detiene.")
                    self.salir()
                    return
            self.latido()
            carpeta = os.path.join(DIR_SESION, self.P.DIR_PETICIONES)
            pendientes = []
            for nombre in os.listdir(carpeta):
                if nombre.endswith(".json") and not nombre.startswith("."):
                    ruta = os.path.join(carpeta, nombre)
                    try:
                        pendientes.append((os.path.getmtime(ruta), nombre, ruta))
                    except OSError:
                        pass
            if pendientes:
                pendientes.sort()
                self.procesar(pendientes[0][2])  # una por tick: el latido no se atrasa
        except Exception:
            _registrar("Error en el bucle del puente:\n" + traceback.format_exc())

    def procesar(self, ruta):
        """Lee, valida y despacha una petición; publica el latido «listo» antes de responder."""
        P = self.P
        nombre = os.path.basename(ruta)[:-5]
        id_ = nombre if P.RE_ID.match(nombre) else None
        t0 = time.time()
        try:
            try:
                bruto = P.leer_json(ruta)
            finally:
                try:
                    os.remove(ruta)
                except OSError:
                    pass
            id_p, accion, datos = P.validar_peticion(bruto, TOKEN)
            if id_ is not None and id_p != id_:
                raise P.ErrorProtocolo("El nombre del archivo no coincide con el id de la petición.")
        except P.ErrorProtocolo as ex:
            if id_:
                self.responder(id_, False, error=str(ex), tipo=ex.tipo, t0=t0)
            _registrar(f"Petición rechazada {nombre}: {ex}")
            return
        except Exception as ex:  # JSON corrupto, archivo ilegible
            if id_:
                self.responder(id_, False, error="Petición ilegible: " + str(ex), tipo="protocolo", t0=t0)
            return

        self.ocupado, self.id_actual, self.inicio_actual = True, id_p, t0
        self.latido(forzar=True)
        try:
            salida = {"ok": True, "resultado": self.manejadores[accion](datos)}
        except P.ErrorProtocolo as ex:
            salida = {"ok": False, "error": str(ex), "tipo": ex.tipo}
        except ValueError as ex:  # ConfigError, CajaError y validaciones del constructor
            salida = {"ok": False, "error": str(ex), "tipo": "validacion"}
        except Exception as ex:
            _registrar(f"Error interno en «{accion}»:\n" + traceback.format_exc())
            salida = {"ok": False, "error": f"Error interno de FreeCAD ({type(ex).__name__}): {ex}",
                      "tipo": "interno"}
        finally:
            self.atendidas += 1
            self.ocupado, self.id_actual, self.inicio_actual = False, None, None
            # El latido «listo» se publica antes que la respuesta: quien la reciba ya ve el
            # puente libre.
            self.latido(forzar=True)
        if salida["ok"]:
            self.responder(id_p, True, resultado=salida["resultado"], t0=t0)
        else:
            self.responder(id_p, False, error=salida["error"], tipo=salida["tipo"], t0=t0)

    def responder(self, id_, ok, resultado=None, error=None, tipo=None, t0=None):
        """Escribe la respuesta de una petición (de forma atómica)."""
        r = self.P.respuesta(TOKEN, id_, ok, resultado, error, tipo, time.time() - (t0 or time.time()))
        self.P.escribir_json_atomico(self.P.ruta_respuesta(DIR_SESION, id_), r)

    # ------------------------------------------------------------ documento
    def documento(self):
        """Documento «Cajas - vista previa» (lo recrea si el usuario lo cerró)."""
        App = self.App
        if NOMBRE_DOC in App.listDocuments():
            doc = App.getDocument(NOMBRE_DOC)
        else:
            doc = App.newDocument(NOMBRE_DOC)
            doc.Label = "Cajas - vista previa (no guardar)"
            self.estado_doc = {}  # el usuario cerró el documento: hay que volver a crear todo
        try:
            self.Gui.setActiveDocument(doc.Name)
        except Exception:
            pass
        return doc

    def _encuadrar(self, doc, isometrica=True):
        """Vista isométrica y encuadre del documento."""
        try:
            vista = self.Gui.getDocument(doc.Name).activeView()
            if isometrica:
                vista.viewIsometric()
            vista.fitAll()
        except Exception:
            pass

    def _preparar_config(self, bruto):
        """Normaliza, completa las medidas de la placa STEP desde la caché y valida."""
        M = self.M
        c = M.normalize(bruto)
        info, size = {}, None
        if c.get("step_path"):
            if not os.path.isfile(c["step_path"]):
                raise M.ConfigError("No se encuentra el archivo STEP: " + c["step_path"], "step_path")
            # Medida que dimensiona la caja (PCB, total o la escrita por el usuario).
            _shape, size, info = self.CF.fit_board(*self.cache.get(c), c)
            c["board_x"], c["board_y"], c["board_z"] = (float(x) for x in size)
        return M.validate(c), size, info

    def _resumen_result(self, r):
        """Resumen de un build para la respuesta."""
        return {"dimensions": r["dimensions"],
                "size": [r["config"][k] for k in ("board_x", "board_y", "board_z")],
                "tongues": r.get("tongues"), "warnings": list(r.get("warnings", [])),
                "fit_interference": r.get("fit_interference"), "collision": r.get("collision"),
                "collision_method": r.get("collision_method"), "fit": r.get("fit"),
                "crossed_faces": list(r.get("crossed_faces", [])),
                **self.CF.board_summary(r["board_info"])}

    # ------------------------------------------------------------ acciones
    def _tomar_colores(self, datos):
        """Guarda los colores pedidos. Devuelve True si cambiaron."""
        if "colores" not in datos:
            return False
        nuevos = dict(self.vista["colores"], **datos["colores"])
        cambio = nuevos != self.vista["colores"]
        self.vista["colores"] = nuevos
        return cambio

    def _colorear(self, doc, placa_nueva=False):
        """Aplica los colores elegidos a base y tapa, y a la placa: los del STEP si los hay
        y están activados; si no, el color elegido. Conserva la transparencia."""
        if not self.App.GuiUp:
            return
        col = self.vista["colores"]

        def rgb(h):
            return tuple(int(h[i:i + 2], 16)/255 for i in (1, 3, 5))
        for nombre, clave in (("CajaBase", "base"), ("CajaTapa", "lid")):
            o = doc.getObject(nombre)
            if o is not None and clave in col:
                vo = o.ViewObject
                t = vo.Transparency
                vo.ShapeColor = rgb(col[clave])
                vo.Transparency = t
        o = doc.getObject("CajaPlaca")
        if o is None:
            return
        vo = o.ViewObject
        t = vo.Transparency
        caras = self.colores_placa
        if col.get("step", True) and caras and len(caras) == len(o.Shape.Faces):
            if placa_nueva or "board" in col:
                vo.DiffuseColor = [tuple(c) for c in caras]
        elif "board" in col:
            vo.ShapeColor = rgb(col["board"])
        vo.Transparency = t

    def a_estado(self, datos):
        """Acción «estado»: versión, documento, caché y peticiones atendidas."""
        docs = list(self.App.listDocuments())
        return {"pid": os.getpid(), "version": ".".join(self.App.Version()[:3]),
                "gui": bool(self.App.GuiUp), "documento": NOMBRE_DOC in docs,
                "hay_caja": self.result is not None, "cache": self.cache.stats(),
                "atendidas": self.atendidas, "activo_s": round(time.time() - self.inicio, 1)}

    def a_cargar_placa(self, datos):
        """Acción «cargar_placa»: lee (o toma de la caché) la placa y la muestra sola."""
        M = self.M
        c = M.normalize(datos["config"])
        if not os.path.isfile(c["step_path"]):
            raise M.ConfigError("No se encuentra el archivo STEP: " + c["step_path"], "step_path")
        t0 = time.time()
        lecturas = self.cache.read_count
        shape, size, info = self.CF.fit_board(*self.cache.get(c), c)
        t_cache = time.time() - t0
        doc = self.documento()
        t1 = time.time()
        self._tomar_colores(datos)
        self.colores_placa = info.get("face_colors")
        r = self.CF.show_board(doc, shape, info["board_key"], self.estado_doc, 0.0,
                               colors=info.get("face_colors"))
        self._colorear(doc, placa_nueva=bool(r.get("board_reassigned")))
        doc.recompute()
        self._encuadrar(doc)
        self.result, self.cfg_clave = None, None  # la caja anterior ya no se muestra
        solidos = info.get("solids")
        caras = info.get("faces")
        return {"size": [float(x) for x in size],
                "solids": solidos if solidos is not None else len(shape.Solids),
                "faces": caras if caras is not None else len(shape.Faces),
                "warnings": list(info.get("warnings", [])),
                "bbox_method": info.get("bbox_method"), "board_key": info["board_key"],
                "colores": bool(info.get("face_colors")),
                **self.CF.board_summary(info),
                "lectura_nueva": self.cache.read_count > lecturas,
                "board_reassigned": bool(r.get("board_reassigned")),
                "tiempos": {"placa": round(t_cache, 3), "documento": round(time.time() - t1, 3)},
                "cache": self.cache.stats()}

    def a_calcular(self, datos):
        """Acción «calcular»: construye la caja y actualiza el documento (solo base y tapa si la placa no cambió)."""
        import json
        for k in ("vista", "visible", "transparencia"):
            if k in datos:
                self.vista[k] = dict(self.vista[k], **datos[k]) if isinstance(datos[k], dict) else datos[k]
        self._tomar_colores(datos)
        t0 = time.time()
        lecturas = self.cache.read_count
        cfg, _size, _info = self._preparar_config(datos["config"])
        t1 = time.time()
        result = self.CF.build(cfg, cache=self.cache)
        t2 = time.time()
        doc = self.documento()
        primera = not self.estado_doc.get("board_key") or "CajaBase" not in [o.Name for o in doc.Objects]
        sinc = self.CF.sync_document(doc, result, self.vista["vista"], self.vista["visible"],
                                     self.vista["transparencia"], self.estado_doc)
        self.colores_placa = result["board_info"].get("face_colors")
        self._colorear(doc, placa_nueva=bool(sinc.get("board_reassigned")))
        doc.recompute()
        if primera or datos.get("encuadrar"):
            self._encuadrar(doc)
        self.result, self.cfg_clave = result, json.dumps(cfg, sort_keys=True)
        out = self._resumen_result(result)
        out.update({"config": cfg, "board_reassigned": bool(sinc.get("board_reassigned")),
                    "sync": sinc, "lectura_nueva": self.cache.read_count > lecturas,
                    "cache": self.cache.stats(), "vista": self.vista,
                    "tiempos": {"placa": round(t1 - t0, 3), "caja": round(t2 - t1, 3),
                                "documento": round(time.time() - t2, 3)}})
        return out

    def a_vista(self, datos):
        """Acción «vista»: vista, visibilidad, transparencias y colores sin recalcular."""
        for k in ("visible", "transparencia"):
            if k in datos:
                self.vista[k] = dict(self.vista[k], **datos[k])
        if self._tomar_colores(datos):
            self._colorear(self.documento(), placa_nueva=True)
        if "vista" in datos:
            self.vista["vista"] = datos["vista"]
        doc = self.documento()
        if self.result is None:
            # Solo hay placa (o nada): se aplica la visibilidad de la placa.
            for o in doc.Objects:
                if o.Name == "CajaPlaca" and self.App.GuiUp:
                    o.ViewObject.Visibility = self.vista["visible"].get("board", True)
            if datos.get("encuadrar"):
                self._encuadrar(doc)
            return {"vista": self.vista, "hay_caja": False}
        self.CF.apply_view(doc, self.result, self.vista["vista"], self.vista["visible"],
                           self.vista["transparencia"])
        doc.recompute()
        if datos.get("encuadrar") or "vista" in datos:
            self._encuadrar(doc)
        return {"vista": self.vista, "hay_caja": True}

    def a_exportar(self, datos):
        """Acción «exportar»: genera los archivos reutilizando la caja calculada si coincide."""
        import json
        lecturas = self.cache.read_count
        cfg, _size, _info = self._preparar_config(datos["config"])
        carpeta = datos["carpeta"]
        if not os.path.isdir(carpeta):
            raise self.M.ConfigError("La carpeta de exportación no existe: " + carpeta, None)
        t0 = time.time()
        reutilizado = self.result is not None and json.dumps(cfg, sort_keys=True) == self.cfg_clave
        result = self.result if reutilizado else self.CF.build(cfg, cache=self.cache)
        t1 = time.time()
        archivos = self.CF.export_result(result, carpeta, cfg)
        doc = self.documento()  # export_result usa un documento propio; volver al nuestro
        out = self._resumen_result(result)
        out.update({"files": archivos, "caja_reutilizada": reutilizado,
                    "lectura_nueva": self.cache.read_count > lecturas, "cache": self.cache.stats(),
                    "tiempos": {"caja": round(t1 - t0, 3), "exportar": round(time.time() - t1, 3)}})
        del doc
        return out

    def a_detectar(self, datos):
        """Propone aberturas para los componentes que atraviesan la caja. Reutiliza la caja
        calculada si la configuración no cambió."""
        import json
        lecturas = self.cache.read_count
        cfg, _size, _info = self._preparar_config(datos["config"])
        t0 = time.time()
        reutilizado = self.result is not None and json.dumps(cfg, sort_keys=True) == self.cfg_clave
        result = self.result if reutilizado else self.CF.build(cfg, cache=self.cache)
        det = self.CF.detect_ports(result, datos["margen"], datos["hasta_union"])
        det.update({"caja_reutilizada": reutilizado, "lectura_nueva": self.cache.read_count > lecturas,
                    "cache": self.cache.stats(), "tiempos": {"detectar": round(time.time() - t0, 3)}})
        return det

    def a_componentes(self, datos):
        """Componentes de la placa (sin el PCB), con su planta en coordenadas de la caja,
        para elegir aberturas en la tapa. No construye la caja."""
        lecturas = self.cache.read_count
        cfg, _size, _info = self._preparar_config(datos["config"])
        if not cfg.get("step_path"):
            raise self.M.ConfigError("La lista de componentes necesita la placa en STEP.", "step_path")
        t0 = time.time()
        local, _s, info = self.CF.fit_board(*self.cache.get(cfg), cfg)
        board = local.translated(self.App.Vector(0, 0, cfg["floor"] + cfg["bottom_gap"]))
        comps = self.CF.list_components(board, info, cfg)
        return {"componentes": comps, "con_nombres": bool(info.get("solid_groups")),
                "lectura_nueva": self.cache.read_count > lecturas, "cache": self.cache.stats(),
                "tiempos": {"componentes": round(time.time() - t0, 3)}}

    def a_captura(self, datos):
        """Acción «captura»: guarda una imagen de la vista 3D."""
        doc = self.documento()
        carpeta = os.path.join(DIR_SESION, "capturas")
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, datos["archivo"])
        self.Gui.updateGui()
        vista = self.Gui.getDocument(doc.Name).activeView()
        vista.saveImage(ruta, datos["ancho"], datos["alto"], "Current")
        return {"archivo": ruta, "objetos": [o.Name for o in doc.Objects],
                "visibles": [o.Name for o in doc.Objects
                             if self.App.GuiUp and o.ViewObject and o.ViewObject.Visibility]}

    def a_detener(self, datos):
        """Acción «detener»: cierra el documento y FreeCAD de forma ordenada."""
        self.deteniendo = True
        self.QtCore.QTimer.singleShot(300, self.salir)
        return {"deteniendo": True}

    def salir(self):
        """Cierra el documento propio y la ventana de FreeCAD."""
        self.deteniendo = True
        self.timer.stop()
        self.latido(forzar=True, estado="deteniendo")
        try:
            if NOMBRE_DOC in self.App.listDocuments():
                self.App.closeDocument(NOMBRE_DOC)  # documento propio: se descarta sin preguntar
        except Exception:
            _registrar("No se pudo cerrar el documento de vista previa:\n" + traceback.format_exc())
        _registrar("Puente detenido.")
        try:
            self.Gui.getMainWindow().close()
        except Exception:
            pass


def _arrancar():
    """Crea el puente solo si FreeCAD lo lanzó la aplicación (variables de entorno presentes)."""
    if not (DIR_SESION and TOKEN and DIR_APP and os.path.isdir(DIR_SESION)):
        try:
            import FreeCAD
            FreeCAD.Console.PrintWarning(
                "cajas_puente_freecad.py solo funciona lanzado por la aplicación Cajas para "
                "circuitos. No se hizo nada.\n")
        except Exception:
            pass
        return None
    if DIR_APP not in sys.path:
        sys.path.insert(0, DIR_APP)
    try:
        return Puente()
    except Exception:
        _registrar("No se pudo iniciar el puente:\n" + traceback.format_exc())
        raise


# FreeCAD ejecuta este archivo como macro; la referencia global mantiene vivo el QTimer.
CAJAS_PUENTE = _arrancar()

"""Prueba de extremo a extremo de la GUI con el visor FreeCAD real.

Abre la ventana (visible), rellena campos como lo haría el usuario y usa el
visor administrado (freecad.exe con GUI + cajas_puente_freecad.py). También
prueba el worker de respaldo, con el visor cerrado. Ejecutar con el Python
habitual (con tkinter):
    python pruebas_cajas\\prueba_gui.py
Salta las partes STEP si no existe el archivo de prueba. Deja capturas del
visor en %TEMP%\\cajas_prueba_gui_capturas para revisarlas.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import cajas_circuitos as G  # noqa: E402
import cajas_modelo as M  # noqa: E402
import cajas_protocolo as P  # noqa: E402

G.guardar_ajustes = lambda datos: None  # no tocar los ajustes reales del usuario
from tkinter import messagebox  # noqa: E402

STEP = Path(r"C:\Users\tupin\Downloads\TEC-8A-24V-PID-HC Step File.stp")
# Placa del usuario con conectores laterales; solo se lee.
ECDL = Path(r"C:/Users/tupin/Documents/adn8834-ecdl/pcb/v2-revision-tierra/ECDL_ADN8834_MOD_GND.step")
CAPTURAS = Path(tempfile.gettempdir()) / "cajas_prueba_gui_capturas"
MENSAJES = []
for nombre in ("showinfo", "showerror", "showwarning"):
    setattr(messagebox, nombre, lambda t="", m="", _n=nombre, **k: MENSAJES.append((_n, t, m)))
messagebox.askyesnocancel = lambda *a, **k: False
messagebox.askyesno = lambda *a, **k: True

RESULTADOS = []


def check(nombre, cond, extra=""):
    RESULTADOS.append((nombre, bool(cond)))
    print(("PASS " if cond else "FAIL ") + nombre + (f" — {extra}" if extra and not cond else ""),
          flush=True)


def esperar(app, limite=600):
    t0 = time.time()
    app.update()
    while ((app.trab.ocupado or app.tvisor.ocupado or app.cadena or app._tarea_vista
            or not app.cola.empty()) and time.time() - t0 < limite):
        app.update()
        time.sleep(0.03)
    for _ in range(10):
        app.update()
        time.sleep(0.02)
    return time.time() - t0


def errores():
    return [m for m in MENSAJES if m[0] == "showerror"]


def captura(app, nombre):
    r = app.sesion.solicitar("captura", {"archivo": nombre + ".png"}, plazo=60)
    CAPTURAS.mkdir(exist_ok=True)
    destino = CAPTURAS / (nombre + ".png")
    shutil.copyfile(r["archivo"], destino)
    return r, destino


def prueba_medida_placa(app):
    """Placa con conectores que sobresalen del PCB (solo lectura): medida del PCB por
    defecto, envolvente total, personalizada y holgura lateral 0 con aviso."""
    app.v["step_path"].set(str(ECDL)); app.v["board_fit"].set("pcb")
    app.v["margin"].set("3"); app.v["view"].set("cerrada")
    app.supports, app.ports = [], []
    app._refrescar_tablas()
    MENSAJES.clear()
    app.medir(); esperar(app, 900)
    check("ECDL: medida por defecto = PCB sin componentes 68 × 68",
          abs(float(app.v["board_x"].get()) - 68) < 0.01 and abs(float(app.v["board_y"].get()) - 68) < 0.01
          and not errores(), f"{app.v['board_x'].get()} {errores()}")
    check("ECDL: medición muestra PCB y total", "PCB sin componentes: 68.00" in app.lbl_medida.cget("text")
          and "77.51" in app.lbl_medida.cget("text"), app.lbl_medida.cget("text"))
    check("ECDL: colores del STEP leídos", "se muestran en el visor" in app.lbl_medida.cget("text"),
          app.lbl_medida.cget("text"))
    check("ECDL: cara inferior del PCB rellenada", abs(float(app.v["pcb_offset"].get()) - 4.42) < 0.01,
          app.v["pcb_offset"].get())
    check("ECDL: campos bloqueados en modo PCB", str(app.e_board[0].cget("state")) == "disabled")

    app.v["margin"].set("0")
    app._validar_en_vivo()
    check("holgura 0 aceptada con aviso de placa ajustada", "ajustada" in app.lbl_val.cget("text")
          and "✗" not in app.lbl_val.cget("text"), app.lbl_val.cget("text"))
    app.vista_previa(); esperar(app, 900)
    avisos = app.warn_box.get("1.0", "end")
    check("caja calculada con holgura 0 (interior 68 mm)", not errores() and
          "68 ×" in app.lbl_dims.cget("text").replace("     ", " "), f"{app.lbl_dims.cget('text')} {errores()}")
    check("aviso de conectores que atraviesan frente e izquierda", "frente, izquierda" in avisos, avisos)
    check("aviso de placa ajustada en el resultado", "ajustada" in avisos, avisos)
    check("vista marcada como actual (PCB)", "✓" in app.lbl_vista.cget("text"), app.lbl_vista.cget("text"))
    _, png = captura(app, "gui_ecdl_pcb_holgura0")
    print("     captura:", png)

    app.v["board_fit"].set("total")
    app.update()
    check("envolvente total en los campos", abs(float(app.v["board_x"].get()) - 77.512) < 0.01,
          app.v["board_x"].get())
    app.vista_previa(); esperar(app, 900)
    avisos = app.warn_box.get("1.0", "end")
    check("total: sin cruces de pared", not errores() and "atraviesan" not in avisos, f"{avisos} {errores()}")

    app.v["board_fit"].set("personalizado")
    app._validar_en_vivo()
    check("personalizado: campos editables", str(app.e_board[0].cget("state")) == "normal")
    app.v["board_x"].set("72"); app.v["board_y"].set("70,5")
    app.vista_previa(); esperar(app, 900)
    check("personalizado: interior 72 × 70,5", not errores() and "72 ×" in app.lbl_dims.cget("text")
          and "70.5" in app.lbl_dims.cget("text"), f"{app.lbl_dims.cget('text')} {errores()}")
    r = app.sesion.solicitar("estado")
    check("cambiar la medida no relee el STEP", r["cache"]["read_count"] == 2, str(r["cache"]))


def esperar_auto(app, limite=300):
    """Espera a que la validación en vivo y la actualización automática terminen."""
    t0 = time.time()
    while time.time() - t0 < limite:
        esperar(app, limite)
        if not (app._tarea_val or app._tarea_auto or app.tvisor.ocupado):
            break
        app.update()
        time.sleep(0.05)
    return time.time() - t0


def prueba_opciones_nuevas(app):
    """Actualización automática, edición en vivo de aberturas, formas, detección de
    aberturas, altura de la unión, cierres (imanes) y apoyos, con la placa ECDL."""
    app.v["step_path"].set(str(ECDL)); app.v["board_fit"].set("pcb"); app.v["margin"].set("0")
    app.v["view"].set("explosionada")
    app.ports, app.supports = [], []
    app._refrescar_tablas()
    app.vista_previa(); esperar(app, 900)
    MENSAJES.clear()
    app.var_auto.set(True)
    # 1) Cambiar un parámetro actualiza el visor solo, sin bloquear la interfaz.
    app.v["top_gap"].set("4")
    bloqueada = []
    t0 = time.time()
    while time.time() - t0 < 60 and not app.tvisor.ocupado:
        app.update(); time.sleep(0.03)
    bloqueada.append(app._ui_bloqueada)
    esperar_auto(app)
    check("cambio de parámetro → visor actualizado solo",
          app.vista_clave == app._clave_vista(app._validar()) and not errores(), f"{errores()}")
    check("la actualización automática no bloquea la interfaz", bloqueada == [False], bloqueada)

    # 2) Detectar aberturas (botón) y que el visor se actualice con ellas.
    app.detectar_aberturas(); esperar(app, 900); esperar_auto(app)
    avisos = app.warn_box.get("1.0", "end")
    check("detectar: 5 aberturas agregadas (4 frente, 1 izquierda)",
          sorted(p["side"] for p in app.ports) == ["frente"]*4 + ["izquierda"] and not errores(),
          f"{app.ports} {errores()}")
    check("tras detectar, nada atraviesa la caja", "atraviesan" not in avisos
          and app.vista_clave == app._clave_vista(app._validar()), avisos)

    # 2b) Aberturas en la tapa sobre componentes elegidos de la lista.
    app.elegir_componentes_tapa(); esperar(app, 900)
    dlg = app.dialogo_componentes
    sugeridos = [c["name"] for c in dlg.comps if c["suggested"]] if dlg else []
    check("lista de componentes con nombres y sugeridos", dlg is not None and len(dlg.comps) > 20
          and sugeridos and not errores(), f"{len(dlg.comps) if dlg else None} {sugeridos} {errores()}")
    print("     sugeridos:", sorted(set(sugeridos)))
    check("los conectores que salen por la pared no se sugieren para la tapa",
          not any("SMA" in n for n in sugeridos) and any(c.get("edge") for c in dlg.comps), sugeridos)
    antes = sum(1 for p in app.ports if p["side"] == "tapa")
    dlg._aceptar()
    app.update()
    tapa = [p for p in app.ports if p["side"] == "tapa"]
    esperar_auto(app)
    check("aberturas de tapa creadas y caja recalculada", len(tapa) > antes and not errores()
          and app.vista_clave == app._clave_vista(app._validar()),
          f"{tapa} {errores()} {app.lbl_vista.cget('text')}")
    if dlg.win.winfo_exists():
        dlg.win.destroy()
    app.elegir_componentes_tapa(); esperar(app, 900)
    dlg = app.dialogo_componentes
    MENSAJES.clear()
    dlg._aceptar()
    check("repetir no duplica aberturas de tapa", sum(1 for p in app.ports if p["side"] == "tapa")
          == len(tapa) and any("No hay aberturas nuevas" in m[2] for m in MENSAJES), MENSAJES)
    if dlg.win.winfo_exists():
        dlg.win.destroy()
    _, png = captura(app, "gui_ecdl_tapa_componentes")
    print("     captura:", png)

    # 3) Edición en vivo de una abertura seleccionada → visor actualizado.
    app.tv_ports.selection_set("0"); app.update()
    app._sel_tabla("ports")
    app.pv["shape"].set(G.FORMA_TXT["ovalada"]); app.pv["width"].set("9")
    esperar_auto(app)
    check("editar la forma de la fila seleccionada la aplica sola",
          app.ports[0]["shape"] == "ovalada" and app.ports[0]["width"] == 9.0
          and app.vista_clave == app._clave_vista(app._validar()) and not errores(),
          f"{app.ports[0]} {errores()} {app.lbl_vista.cget('text')}")
    app.pv["shape"].set(G.FORMA_TXT["circular"]); app.pv["width"].set("8")
    esperar_auto(app)
    check("forma circular desde la GUI", app.ports[0]["shape"] == "circular"
          and app.ports[0]["height"] == 8.0 and not errores(), app.ports[0])
    app._editar_tabla("ports", "none")

    # 4) Unión a la altura de los conectores + imanes + repisa + pisadores.
    app._union_aberturas()
    check("unión a la altura de las aberturas", abs(float(app.v["split_height"].get().replace(",", "."))
                                                    - app.ports[1]["v"]) < 0.01, app.v["split_height"].get())
    app.v["closure"].set("imanes"); app.v["board_rest"].set("repisa"); app.var_pisadores.set(True)
    esperar_auto(app)
    avisos = app.warn_box.get("1.0", "end")
    check("imanes + repisa + pisadores calculados solos", "Imanes" in avisos and not errores()
          and app.vista_clave == app._clave_vista(app._validar()), f"{avisos} {errores()}")
    _, png = captura(app, "gui_ecdl_imanes_aberturas")
    print("     captura:", png)
    app.v["view"].set("cerrada"); esperar_auto(app)
    _, png = captura(app, "gui_ecdl_imanes_cerrada")
    print("     captura:", png)

    # 5) Un error de la actualización automática no abre diálogos.
    MENSAJES.clear()
    app.v["magnet_height"].set("30")
    esperar_auto(app)
    check("configuración inválida: sin diálogo ni recálculo", not MENSAJES
          and "✗" in app.lbl_val.cget("text"), f"{MENSAJES} {app.lbl_val.cget('text')}")
    app.v["magnet_height"].set("2")
    app.v["closure"].set("friccion"); app.v["split_height"].set("0"); app.var_pisadores.set(False)
    app.v["board_rest"].set("esquinas"); app.v["top_gap"].set("3"); app.v["view"].set("separada")
    app.ports = []
    app._refrescar_tablas(); app._cambio()
    esperar_auto(app)
    app.var_auto.set(False)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="prueba gui ñandú "))
    app = G.crear_app()
    app.update()
    app.var_auto.set(False)   # las secciones clásicas calculan a mano; la automática se prueba aparte
    pid_visor = None
    try:
        check("ventana visible", app.winfo_viewable() and app.winfo_width() > 500)
        check("FreeCAD localizado", app._exe_freecad() is not None, str(app.python_fc))
        check("sin Matplotlib", "matplotlib" not in sys.modules)
        # Personalización: idioma, tema, tamaño del texto y colores del visor (sin FreeCAD).
        app.var_idioma.set("en"); app._cambiar_idioma()
        app.var_tema.set("claro"); app._cambiar_tema()
        app.var_texto.set("grande"); app._cambiar_texto()
        app.update()
        check("menú: inglés, tema claro y texto grande", app.btn_menu.cget("text") == "☰  Options"
              and "Unsaved project" in app.title() and G.UI["bg"] == G.TEMAS["claro"]["bg"]
              and app.fuentes["base"].cget("size") == 12, app.title())
        app.var_idioma.set("es"); app._cambiar_idioma()
        app.var_tema.set("oscuro"); app._cambiar_tema()
        app.var_texto.set("normal"); app._cambiar_texto()
        app.update()
        check("vuelta a español, oscuro y normal", app.btn_menu.cget("text") == "☰  Opciones"
              and "Proyecto sin guardar" in app.title() and app.fuentes["base"].cget("size") == 10)
        dlg_col = app.dialogo_colores(); app.update(); dlg_col.destroy()
        check("colores del visor en cada vista", app._datos_vista()["colores"]
              == {"base": "#4aadd1", "lid": "#f2ab4d", "board": "#38a359", "step": True})
        app._validar_en_vivo()
        check("defaults válidos", "✓" in app.lbl_val.cget("text"), app.lbl_val.cget("text"))
        app.v["margin"].set("3,5")
        app._validar_en_vivo()
        check("coma decimal aceptada", "✓" in app.lbl_val.cget("text"), app.lbl_val.cget("text"))
        app.v["wall"].set("2")
        app._validar_en_vivo()
        check("pared 2 mm rechazada con etiqueta", "✗" in app.lbl_val.cget("text")
              and "Pared" in app.lbl_val.cget("text"), app.lbl_val.cget("text"))
        app.v["wall"].set("3")

        app.pv["side"].set("Frente (−Y)")
        for k, val in (("u", "0"), ("v", "11,5"), ("width", "10"), ("height", "6")):
            app.pv[k].set(val)
        app._editar_tabla("ports", "add")
        app.sv["x"].set("25"); app.sv["y"].set("25"); app.sv["height"].set("2,5")
        app._editar_tabla("supports", "add")
        check("tablas con 1 abertura y 1 soporte", len(app.ports) == 1 and len(app.supports) == 1)

        # --- Visor: abrir y calcular (manual)
        MENSAJES.clear()
        app.vista_previa()
        dur = esperar(app, 300)
        pid_visor = app.sesion.proc.pid if app._visor_vivo() else None
        check(f"visor abierto y caja manual calculada ({dur:.1f} s)",
              pid_visor and app.vista_clave and not errores(), str(errores()))
        check("estado del visor «conectado»", "conectado" in app.lbl_visor.cget("text"),
              app.lbl_visor.cget("text"))
        check("vista marcada como actual", "✓" in app.lbl_vista.cget("text"), app.lbl_vista.cget("text"))
        previo = app.v["top_gap"].get()
        app.v["top_gap"].set("4,5")
        app._validar_en_vivo()
        check("vista marcada desactualizada tras cambio", "anterior" in app.lbl_vista.cget("text"),
              app.lbl_vista.cget("text"))
        app.v["top_gap"].set(previo)
        app.v["view"].set("cerrada")
        esperar(app)
        app._validar_en_vivo()
        check("cambiar la vista no desactualiza la geometría", "✓" in app.lbl_vista.cget("text"),
              app.lbl_vista.cget("text"))

        # Soporte que choca con la placa: error de validación sin colgarse
        app.supports.append({"x": 0.0, "y": 0.0, "height": 8.0, "diameter": 6.0, "hole": 0.0})
        MENSAJES.clear()
        app.vista_previa()
        esperar(app)
        check("soporte en colisión rechazado", errores() and app._visor_vivo(), "sin error")
        app.supports.pop()

        proy = tmp / "proyecto ñ.cajas.json"
        check("guardar proyecto", app._guardar_en(str(proy)) and proy.is_file())
        cfg = M.load_project(proy)
        check("proyecto conserva abertura y coma", cfg["ports"][0]["v"] == 11.5 and cfg["margin"] == 3.5)

        if STEP.is_file():
            app.ports.clear()
            app.supports.clear()
            app._refrescar_tablas()
            app.v["board_x"].set("10"); app.v["board_y"].set("10"); app.v["board_z"].set("5")
            app.v["source"].set("step")
            app.v["step_path"].set(str(STEP))
            app.v["up_axis"].set("Z"); app.v["rotation"].set("0")
            app.v["view"].set("explosionada")
            MENSAJES.clear()
            t0 = time.time()
            app.medir()
            esperar(app, 900)
            t_carga = time.time() - t0
            r0 = app.sesion.solicitar("estado")
            check(f"STEP cargado en el visor ({t_carga:.1f} s)", app.medida and
                  r0["cache"]["read_count"] == 1 and not errores(), f"{r0['cache']} {errores()}")
            check("medidas actualizadas antes de validar",
                  abs(float(app.v["board_x"].get()) - 44.0) < 0.01, app.v["board_x"].get())
            tiempos = []
            for margen in ("3", "4", "5"):
                app.v["margin"].set(margen)
                t0 = time.time()
                app.vista_previa()
                esperar(app, 900)
                tiempos.append(time.time() - t0)
            r1 = app.sesion.solicitar("estado")
            check("recalcular holguras no relee el STEP (Part.read = 1)",
                  r1["cache"]["read_count"] == 1 and "placa en memoria" in app.lbl_info.cget("text")
                  and not errores(), f"{r1['cache']} {app.lbl_info.cget('text')} {errores()}")
            print(f"     carga inicial {t_carga:.2f} s; actualizaciones "
                  + ", ".join(f"{t:.2f} s" for t in tiempos), flush=True)
            check("actualización mucho más rápida que la carga", max(tiempos) < t_carga / 2,
                  f"{tiempos} vs {t_carga}")
            _, png = captura(app, "gui_step_explosionada")
            print("     captura:", png)
            for vista in ("separada", "cerrada"):
                app.v["view"].set(vista)
                esperar(app)
            app.ver["lid"].set(False); app._programar_vista(); esperar(app)
            r, png = captura(app, "gui_step_cerrada_sin_tapa")
            check("ocultar la tapa desde la GUI", "CajaTapa" not in r["visibles"]
                  and "CajaPlaca" in r["visibles"], str(r["visibles"]))
            app.ver["lid"].set(True); app._programar_vista(); esperar(app)

            app.v["up_axis"].set("X"); app.v["rotation"].set("90")
            app.vista_previa()
            esperar(app, 900)
            r2 = app.sesion.solicitar("estado")
            check("cambio de orientación sin releer", r2["cache"]["read_count"] == 1 and not errores(),
                  f"{r2['cache']} {errores()}")
            check("medidas con eje X", abs(float(app.v["board_z"].get()) - 44.0) < 0.01,
                  app.v["board_z"].get())
            app.v["up_axis"].set("Z"); app.v["rotation"].set("0")
            app.vista_previa(); esperar(app, 900)

            # Exportación por el visor (reutiliza la caja calculada)
            app.var_salida.set(str(tmp / "salidas cajas"))
            app.var_stl.set(True); app.var_step.set(True)
            MENSAJES.clear()
            app.generar()
            esperar(app, 900)
            c1 = app.ultima_salida
            archivos = sorted(p.name for p in Path(c1).iterdir()) if c1 else []
            check("generación desde el visor", c1 and {"Caja.FCStd", "Base.stl", "Tapa.stl", "Base.step",
                                                       "Tapa.step"} <= set(archivos) and not errores(),
                  f"{archivos} {errores()}")
            r3 = app.sesion.solicitar("estado")
            check("exportar no relee el STEP", r3["cache"]["read_count"] == 1, str(r3["cache"]))
            check("proyecto y macro en la carpeta", any(a.endswith(".FCMacro") for a in archivos)
                  and any(a.endswith(".cajas.json") for a in archivos), str(archivos))

            if ECDL.is_file():
                prueba_medida_placa(app)
                prueba_opciones_nuevas(app)
            else:
                print("SKIP medida de placa: no existe", ECDL)
            app.v["step_path"].set(str(STEP)); app.v["board_fit"].set("pcb"); app.v["margin"].set("3")
            app.vista_previa(); esperar(app, 900)
        else:
            print("SKIP pruebas STEP: no existe", STEP)

        # --- Reinicio (recuperación) y cierre del visor
        lanz = app.sesion.lanzamientos
        app.reiniciar_visor()
        esperar(app, 300)
        nuevo = app.sesion.proc.pid if app._visor_vivo() else None
        check("reiniciar visor: proceso nuevo, el anterior terminado",
              nuevo and nuevo != pid_visor and not P.proceso_vivo(pid_visor)
              and app.sesion.lanzamientos == lanz + 1, f"{pid_visor}->{nuevo}")
        pid_visor = nuevo
        app.cerrar_visor()
        esperar(app, 60)
        check("cerrar visor", not app._visor_vivo() and not P.proceso_vivo(pid_visor))

        # --- Worker de respaldo con el visor cerrado
        app.var_salida.set(str(tmp / "salidas worker"))
        app.var_step.set(False)
        MENSAJES.clear()
        app.generar()
        esperar(app, 900)
        c2 = app.ultima_salida
        ok = c2 and (Path(c2) / "Caja.FCStd").is_file() and not errores()
        check("generación con el worker (visor cerrado)", ok and not app._visor_vivo(), str(errores()))
        if ok and STEP.is_file():
            macro = next(Path(c2).glob("*.FCMacro"))
            texto = macro.read_text(encoding="utf-8")
            check("macro STEP contiene la ruta", str(STEP) in texto or json.dumps(str(STEP))[1:-1] in texto)
    finally:
        app.modificado = False
        app._cerrar()
        shutil.rmtree(tmp, ignore_errors=True)
    if pid_visor:
        time.sleep(1)
        check("ningún visor queda vivo al cerrar la aplicación", not P.proceso_vivo(pid_visor))
    fallos = [n for n, ok in RESULTADOS if not ok]
    print(f"\n{len(RESULTADOS) - len(fallos)}/{len(RESULTADOS)} PASS")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())

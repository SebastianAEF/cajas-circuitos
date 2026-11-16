"""Idioma de la interfaz de «Cajas para circuitos» (español / inglés).

La interfaz se escribe en español; `traducir` devuelve el texto en el idioma activo
buscándolo en EN (español → inglés). Un texto sin traducción se muestra en español.
Los mensajes de validación y de FreeCAD se generan en español y no se traducen.
"""
from __future__ import annotations

IDIOMAS = (("es", "Español"), ("en", "English"))
_actual = "es"

EN = {}       # español -> inglés; se completa abajo
ES = {}       # inglés -> español (inverso)


def fijar(codigo):
    """Elige el idioma activo («es» o «en»)."""
    global _actual
    _actual = codigo if codigo in dict(IDIOMAS) else "es"


def actual():
    """Código del idioma activo."""
    return _actual


def traducir(texto):
    """Texto en el idioma activo (acepta el texto en español o ya traducido)."""
    if _actual == "en":
        return EN.get(texto, texto)
    return ES.get(texto, texto)


def _registrar(pares):
    """Añade pares español → inglés (y el inverso) a los diccionarios."""
    for es, en in pares.items():
        EN[es] = en
        ES.setdefault(en, es)


_registrar({
    # Barra superior y menú
    "Nuevo": "New", "Abrir…": "Open…", "Guardar": "Save", "Guardar como…": "Save as…",
    "Ayuda": "Help", "FreeCAD…": "FreeCAD…", "Opciones": "Options", "Idioma": "Language",
    "Tema": "Theme", "Oscuro": "Dark", "Claro": "Light", "Tamaño del texto": "Text size",
    "Pequeño": "Small", "Normal": "Normal", "Grande": "Large",
    "Colores en FreeCAD…": "FreeCAD colors…", "Restablecer apariencia": "Reset appearance",
    "Actualizar el visor automáticamente": "Update the viewer automatically",
    # Pestañas
    "Placa": "Board", "Caja": "Box", "Cierre": "Closure", "Aberturas": "Openings",
    "Soportes": "Standoffs", "Exportar": "Export",
    # Placa
    "Origen de la placa": "Board source",
    "Importar placa STEP (.step / .stp)": "Import STEP board (.step / .stp)",
    "Examinar…": "Browse…",
    "Medidas manuales (la placa es un bloque)": "Manual size (the board is a block)",
    "Eje vertical del STEP": "STEP vertical axis", "Giro en torno a Z": "Rotation about Z",
    "Medir STEP con FreeCAD": "Measure STEP with FreeCAD",
    "Medidas que dimensionan la caja": "Size used for the box",
    "Solo el PCB, sin componentes (recomendado)": "Bare PCB only, no components (recommended)",
    "Envolvente con todos los componentes": "Envelope including all components",
    "Personalizadas": "Custom", "Ancho X": "Width X", "Largo Y": "Length Y", "Alto Z total": "Total height Z",
    "Con «Solo el PCB», lo que sobresale (p. ej. conectores laterales) atraviesa la pared: se avisa y se "
    "puede detectar en Aberturas. Las personalizadas se centran en el PCB.":
        "With «Bare PCB only», anything sticking out (e.g. side connectors) goes through the wall: you "
        "are warned and can detect it in Openings. Custom sizes are centred on the PCB.",
    "Cara inferior del PCB": "PCB bottom face", "Desde el punto más bajo": "From the lowest point",
    "Se rellena al medir el STEP.": "Filled in when measuring the STEP.",
    # Caja
    "Holguras alrededor de la placa": "Clearances around the board",
    "Holgura lateral": "Side clearance", "Holgura inferior": "Bottom clearance",
    "Holgura superior": "Top clearance", "Por lado. 0 = placa a presión.": "Per side. 0 = press fit.",
    "Bajo el punto más bajo.": "Below the lowest point.",
    "Sobre el componente más alto.": "Above the tallest component.",
    "Paredes": "Walls", "Pared lateral": "Side wall", "≥ 3 mm con lengüeta o labio.": "≥ 3 mm with tongue or lip.",
    "Piso": "Floor", "Techo de la tapa": "Lid roof", "Radio de esquinas": "Corner radius", "Exterior.": "Outside.",
    "Apoyo de la placa": "Board support",
    "Apoyos en las esquinas bajo el PCB (recomendado)": "Corner supports under the PCB (recommended)",
    "Repisa a lo largo del borde del PCB": "Ledge along the PCB edge", "Sin apoyos": "No supports",
    "Ancho bajo el PCB": "Width under the PCB", "Cuánto entra bajo el borde.": "How far it goes under the edge.",
    "Pisadores en la tapa (sujetan el PCB desde arriba)": "Hold-down posts in the lid (press the PCB from above)",
    "Los apoyos suben hasta la cara inferior del PCB para que no flote ni se incline; donde hay "
    "componentes debajo se recortan solos.":
        "Supports rise up to the PCB bottom face so it does not float or tilt; they are trimmed "
        "automatically where there are components underneath.",
    "Coordenadas: X/Y desde el centro de la caja; Z desde la base exterior.":
        "Coordinates: X/Y from the box centre; Z from the outer bottom.",
    # Cierre
    "Tipo de cierre (sin tornillos)": "Closure type (screwless)",
    "Lengüetas cortas con nervios de fricción": "Short tongues with friction ribs",
    "Labio continuo a presión": "Continuous press-fit lip",
    "Imanes en las esquinas + labio de alineación": "Corner magnets + alignment lip",
    "Tapa apoyada (sin retención)": "Resting lid (no retention)",
    "Unión entre base y tapa": "Base–lid split", "Altura de la unión": "Split height",
    "Desde la base exterior; 0 = automática.": "From the outer bottom; 0 = automatic.",
    "A la altura de las aberturas laterales": "At the height of the side openings",
    "Falda de la tapa": "Lid skirt", "Solo con unión automática.": "Only with automatic split.",
    "A la altura de los conectores, sus aberturas quedan mitad en la base y mitad en la tapa: la placa "
    "entra en vertical.":
        "At connector height, their openings are half in the base and half in the lid: the board "
        "drops in vertically.",
    "Lengüeta o labio": "Tongue or lip", "Altura": "Height", "Entre 1 y 4 mm.": "Between 1 and 4 mm.",
    "Juego por lado": "Clearance per side", "0,10–0,25 típico en FDM.": "0.10–0.25 typical for FDM.",
    "Interferencia de nervios": "Rib interference", "Por lado; 0 los desactiva.": "Per side; 0 disables them.",
    "Imanes (cilindro plano)": "Magnets (flat cylinder)", "Diámetro": "Diameter", "Alto": "Height",
    "Holgura del alojamiento": "Pocket clearance", "Por lado; 0,1–0,2 a presión.": "Per side; 0.1–0.2 press fit.",
    "4 en la base y 4 en la tapa, en torres que sobresalen en las esquinas. Colócalos de modo que cada "
    "par se atraiga.":
        "4 in the base and 4 in the lid, in towers protruding at the corners. Place them so each pair "
        "attracts.",
    "El ajuste real depende de la impresora: imprime una prueba y ajusta el juego.":
        "The real fit depends on the printer: print a test and adjust the clearance.",
    # Aberturas
    "Cara": "Face", "Forma": "Shape", "Ancho": "Width", "Agregar": "Add", "Actualizar": "Update",
    "Eliminar": "Delete", "Deseleccionar": "Deselect", "Editar abertura": "Edit opening",
    "Ancho / Ø": "Width / Ø",
    "Abrir hasta la unión (entra en vertical; la tapa la cierra)":
        "Open up to the split (drops in vertically; the lid closes it)",
    "Laterales: u = posición horizontal desde el centro de la cara; v = altura desde la base exterior. "
    "Tapa: u = X, v = Y. Con una fila seleccionada, los cambios se aplican a ella.":
        "Sides: u = horizontal position from the face centre; v = height from the outer bottom. Lid: "
        "u = X, v = Y. With a row selected, changes apply to it.",
    "Detectar aberturas en las paredes": "Detect openings in the walls",
    "Busca los componentes que atraviesan la caja (conectores laterales) y agrega una abertura para "
    "cada uno. Las que ya existen no se repiten.":
        "Finds components that go through the box (side connectors) and adds an opening for each. "
        "Existing ones are not repeated.",
    "Juego alrededor": "Clearance around",
    "Abrirlas hasta la unión (insertar la placa en vertical)": "Open them up to the split (insert the board vertically)",
    "Detectar aberturas": "Detect openings",
    "Aberturas en la tapa sobre componentes": "Lid openings over components",
    "Elige de una lista los componentes a los que quieres llegar desde arriba (conectores, trimmers…). "
    "Vienen marcados los que lo parecen por su nombre. La abertura sigue la planta del componente.":
        "Pick from a list the components you want to reach from above (connectors, trimmers…). Those "
        "that look like it by name come pre-checked. The opening follows the component footprint.",
    "Elegir componentes…": "Choose components…",
    "Frente (−Y)": "Front (−Y)", "Fondo / trasera (+Y)": "Back (+Y)", "Izquierda (−X)": "Left (−X)",
    "Derecha (+X)": "Right (+X)", "Tapa (techo)": "Lid (top)",
    "Rectangular": "Rectangular", "Redondeada": "Rounded", "Circular (Ø = ancho)": "Circular (Ø = width)",
    "Ovalada": "Oval",
    # Soportes
    "Postes cilíndricos sobre el piso. x, y desde el centro de la caja; altura desde la cara superior "
    "del piso. Agujero 0 = macizo (no perfora el piso).":
        "Cylindrical posts on the floor. x, y from the box centre; height from the top of the floor. "
        "Hole 0 = solid (never pierces the floor).",
    "Agujero": "Hole", "Agujero Ø": "Hole Ø", "Editar soporte": "Edit standoff",
    "Altura sugerida": "Suggested height",
    "La colisión con la placa se comprueba al calcular en FreeCAD.":
        "Collision with the board is checked when computing in FreeCAD.",
    # Exportar
    "Proyecto": "Project", "Nombre": "Name", "Generar modelos con FreeCAD": "Generate models with FreeCAD",
    "STL de base y tapa (orientados para imprimir)": "Base and lid STL (oriented for printing)",
    "STEP de base y tapa": "Base and lid STEP", "Carpeta de salida": "Output folder",
    "Cada generación crea una subcarpeta nueva (nombre_fecha_hora) con Caja.FCStd, las mallas, el "
    "proyecto y la macro. Nunca se sobrescribe nada.":
        "Each run creates a new subfolder (name_date_time) with Caja.FCStd, the meshes, the project and "
        "the macro. Nothing is ever overwritten.",
    "Generar en carpeta nueva": "Generate in a new folder", "Abrir última carpeta": "Open last folder",
    "Macro autónoma de FreeCAD": "Standalone FreeCAD macro",
    "Un .FCMacro con el constructor y la configuración: en FreeCAD crea un documento nuevo con base, "
    "tapa y placa. Para cambiar la caja, edita CONFIG y vuelve a ejecutarla.":
        "A .FCMacro with the builder and the settings: in FreeCAD it creates a new document with base, "
        "lid and board. To change the box, edit CONFIG and run it again.",
    "Exportar macro…": "Export macro…",
    # Panel derecho
    "Medidas y validación": "Dimensions and validation", "Visor 3D · FreeCAD": "3D viewer · FreeCAD",
    "Abrir visor": "Open viewer", "Reconectar": "Reconnect", "Reiniciar": "Restart", "Cerrar": "Close",
    "Ventana de FreeCAD aparte con los sólidos reales: gira y acerca con el ratón allí. No guarda nada; "
    "exporta desde la pestaña Exportar.":
        "A separate FreeCAD window with the real solids: rotate and zoom there with the mouse. It saves "
        "nothing; export from the Export tab.",
    "Calcular caja en FreeCAD": "Compute box in FreeCAD", "Actualizar automáticamente": "Update automatically",
    "Vista": "View", "Separada (impresión)": "Separated (print)", "Cerrada": "Closed",
    "Explosionada": "Exploded", "Encuadrar": "Fit", "Mostrar": "Show", "Base": "Base", "Tapa": "Lid",
    "Transparencia base": "Base transparency", "Transparencia tapa": "Lid transparency",
    "Avisos y resultado": "Warnings and result", "Cancelar": "Cancel", "Listo.": "Ready.",
    "● Visor cerrado": "● Viewer closed", "● Abriendo FreeCAD…": "● Opening FreeCAD…",
    "● Visor conectado": "● Viewer connected", "● FreeCAD calculando…": "● FreeCAD computing…",
    "● FreeCAD no responde": "● FreeCAD not responding", "● FreeCAD se cerró": "● FreeCAD closed",
    "Proyecto sin guardar": "Unsaved project", "Cajas para circuitos": "Circuit boxes",
    "Interior": "Inner", "Exterior": "Outer",
    "✓ Configuración válida (las colisiones reales se comprueban al calcular en FreeCAD).":
        "✓ Valid settings (real collisions are checked when computing in FreeCAD).",
    "Configuración válida.": "Valid settings.",
    "✓ El visor muestra los sólidos exactos de esta configuración.":
        "✓ The viewer shows the exact solids for these settings.",
    "⚠ El visor muestra una caja anterior: pulsa «Calcular caja en FreeCAD».":
        "⚠ The viewer shows an older box: press «Compute box in FreeCAD».",
    # Diálogo de componentes
    "Aberturas en la tapa": "Lid openings",
    "Componentes a los que quieres llegar desde arriba": "Components you want to reach from above",
    "Buscar": "Search", "Ninguno": "None", "Solo sugeridos": "Suggested only", "Componente": "Component",
    "Centro X, Y": "Centre X, Y", "Planta": "Footprint", "Altura sup.": "Top height", "Abertura": "Opening",
    "Circular": "Circular", "Sale por pared": "Exits through wall", "Crear aberturas": "Create openings",
    # Colores
    "Colores en FreeCAD": "FreeCAD colors", "Placa (sin colores del STEP)": "Board (without STEP colors)",
    "Cambiar…": "Change…", "Usar los colores del STEP para la placa (si los trae)":
        "Use the STEP colors for the board (if present)",
    "Restablecer": "Reset",
    "Se aplican en el visor al momento.": "Applied in the viewer immediately.",
})

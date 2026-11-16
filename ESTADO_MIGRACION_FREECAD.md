# Estado de la migración a FreeCAD como visor y generador

**Estado: TERMINADA** (2026-11-15, de 08:52 a ~09:35). El encargo era el de `C:\Users\tupin\Downloads\coil_building\RETOMAR_MIGRACION_FREECAD.md`.

Hubo un respaldo antes de editar: `respaldo_antes_migracion_20261115_085234/`, con `cajas_circuitos.py`, `cajas_freecad.py`, `cajas_modelo.py`, el lanzador, `README_CAJAS.md`, `.gitignore` y `pruebas_cajas/*.py` en su versión previa.

## Qué cambió

- El **visor Matplotlib se eliminó**. La GUI tkinter conserva todos los formularios y, en lugar de la gráfica, tiene un panel «Visor 3D: FreeCAD» con:
  - estado del visor;
  - botones Abrir/Reconectar, Reiniciar y Cerrar;
  - «Calcular caja en FreeCAD»;
  - vistas separada, cerrada y explosionada, y Encuadrar;
  - mostrar u ocultar base, tapa y placa, con transparencia de base y tapa;
  - tiempos y número de lecturas del STEP.
  La GUI ya no importa matplotlib ni numpy.
- **Visor administrado.** La aplicación lanza `freecad.exe --user-cfg <copia privada> cajas_puente_freecad.py`. El FreeCAD del usuario y sus preferencias no se tocan.
  - El puente trabaja solo en su documento «Cajas - vista previa (no guardar)» (objetos CajaBase, CajaTapa, CajaPlaca y CajaParametros).
  - Si se ejecuta sin las variables de entorno de la aplicación, no hace nada.
  - Si muere la aplicación, se cierra solo (en las pruebas, ~2 s).
- **Puente.** Peticiones y respuestas JSON por archivos en un directorio privado de la sesión (`cajas_protocolo.py`):
  - escritura atómica, token de sesión, id validado y plazo de caducidad;
  - acciones cerradas: estado, cargar_placa, calcular, vista, exportar, captura y detener;
  - sin red y sin eval/exec;
  - un QTimer de 100 ms atiende las peticiones y un latido indica listo, ocupado (con id) o deteniendo.
- **Sesión** (`cajas_sesion_freecad.py`):
  - «Conectado» solo se muestra tras una respuesta real del puente.
  - Detecta un visor cerrado, sin latido, con plazo vencido u ocupado.
  - `iniciar()` reutiliza el visor si responde; si está colgado lo termina y lanza otro. Nunca hay dos.
  - Una sola petición a la vez: una segunda simultánea da «ocupado».
  - No bloquea Tk: todo corre en un hilo y vuelve por la cola.
- **Caché** (`cajas_freecad.BoardCache`, del agente de geometría):
  - Separa los originales leídos (máx. 2) de las orientaciones, que son copias (máx. 4).
  - Se invalida por ruta, tamaño y mtime, y cuenta las llamadas reales a `Part.read`.
  - Sin temporales STEP por cálculo.
  - `sync_document` regenera solo base y tapa. La placa se reasigna solo si cambia la placa u orientación, y la vista se aplica con Placement.
  - `export_result` reutiliza el resultado calculado.
  - Se conservan todas las validaciones, las colisiones reales, el cierre sin tornillos y la orientación de impresión.
- **Worker anterior.** Se mantiene, con su protocolo y pruebas, para exportar y medir cuando el visor está cerrado.
- **Macro autónoma, proyectos JSON y generaciones en carpeta nueva:** sin cambios de comportamiento.

## Rendimiento medido (FreeCAD 1.0.2 GUI real, mismo STEP de prueba)

| Operación | Tiempo | `Part.read` acumulado |
|---|---|---|
| Arrancar el visor | 3,2 s | – |
| Primera carga del STEP (44 × 59 × 18,805 mm) | 8,3–8,8 s (Part.read ≈ 6,4 s) | 1 |
| Recalcular cambiando holgura / pared / abertura / soporte | 0,84–1,73 s | **1** (sin relectura) |
| Cambiar la orientación (eje X, giro 90°) | 2,5–2,6 s | 1 |
| Exportar FCStd+STL+STEP reutilizando la caja | 2,2 s | 1 |
| STEP cambiado en disco (mtime) | se relee | +1 |
| Placa del usuario `ECDL_ADN8834_MOD_GND.step` (77,5 × 77,5 × 17,07 mm): carga / recálculos | 3,1 s / 0,9–1,5 s | 1 |

La placa del usuario solo se leyó y no se modificó.

## Pruebas ejecutadas (resultados reales de la última corrida)

- `python -m unittest discover -s pruebas_cajas -p "test_*.py"`: **72 OK**, sin fallos esperados, en 3 corridas seguidas. Incluye:
  - `test_puente_protocolo.py` (33): validación del protocolo, escritura atómica, `proceso_vivo` y el cliente con un puente falso (ocupado, cancelada, sin_respuesta, cerrada, plazo, token ajeno, respuestas tardías);
  - `test_regresiones_cajas.py`, adaptado a la GUI nueva;
  - `test_cajas_modelo.py`.
- `pruebas_cajas/prueba_puente_freecad.py`, con FreeCAD GUI real: **16/16 PASS**.
  - **Placa en memoria:** carga y recálculos con `read_count` = 1; orientación sin releer; cambio en disco con ruta con «ñ» y espacios.
  - **Vistas:** visibilidad y transparencia, con capturas revisadas a ojo.
  - **Exportación:** reutiliza la caja, no sobrescribe, y el FCStd se reabre.
  - **Errores de protocolo:** JSON corrupto, token incorrecto, acción no permitida, petición caducada, ruta de captura y validación.
  - **Concurrencia.**
  - **Visor muerto, relanzar, reutilizar, visor colgado (`NtSuspendProcess`), cierre ordenado, PID padre.**
  - **Procesos:** no queda ningún visor propio y el FreeCAD del usuario sigue intacto.
- `pruebas_cajas/prueba_gui.py`, con la ventana tkinter visible y FreeCAD GUI real: **30/30 PASS**.
  - Validación en vivo, abrir el visor y calcular, aviso de vista desactualizada, colisión de soporte, guardar el proyecto.
  - STEP: medidas actualizadas antes de validar, sin relectura y con ≈ 1,3–1,7 s por actualización frente a 8,6 s de la carga.
  - Ocultar la tapa, cambiar la orientación, exportar desde el visor sin releer.
  - Reiniciar el visor (proceso nuevo, el anterior terminado), cerrar el visor, generar con el worker y el visor cerrado.
  - Ningún visor vivo al cerrar la aplicación.
- **Por el agente de geometría (Python de FreeCAD sin GUI):**
  - `prueba_cache_freecad.py`: 10/10;
  - `prueba_freecad_real.py`: 17/17;
  - `prueba_regresiones_freecad.py`: OK.
  Desde entonces `cajas_freecad.py` no cambió.
- **Inspección visual:**
  - capturas del visor con la placa de prueba y la del usuario: placa con sus sólidos nativos, base con lengüetas, tapa en vista explosionada, cerrada translúcida y oculta;
  - captura de la ventana tkinter con el panel nuevo.

## Bugs encontrados y corregidos durante la migración

- **Carrera respuesta–latido:** el puente publicaba la respuesta antes del latido «listo» y la GUI mostraba «calculando». Ahora publica primero el latido.
- **`os.replace` en Windows:** fallaba con PermissionError si el otro lado leía el archivo y dejaba un `.tmp` suelto. Ahora reintenta y limpia el temporal.
- **Lectura fallida del latido:** una lectura fallida puntual podía dar «sin_respuesta» con el puente ocupado. Ahora se usa el último latido válido.
- **Visor muerto:** `solicitar()` daba «sin_sesion» en vez de «cerrada».
- **Limpieza de la sesión:** a veces dejaba el directorio vacío mientras FreeCAD terminaba. Ahora reintenta.

## Limitaciones reales

- **Cancelación:** mientras OCC calcula (lectura del STEP o booleanas), el hilo principal de FreeCAD no atiende nada y su ventana no se redibuja.
  - «Cancelar» solo deja de esperar; la respuesta tardía se descarta.
  - Para cortar un cálculo de verdad está «Reiniciar visor», que termina el proceso y pierde la placa en memoria, pero no los proyectos.
- **Primera carga:** sigue costando lo que tarda `Part.read` (unos 6–9 s con el STEP de prueba). La mejora está en las actualizaciones posteriores.
- **Cierre de la ventana:** no se probó cerrando la ventana de FreeCAD con un clic real del usuario. Se probó terminando el proceso, que la aplicación detecta como «cerrado».
- **Modo de instancia única:** si FreeCAD está configurado así, el visor no arranca (el puente no recibe su entorno). La aplicación lo informa como error de arranque. Hoy no está activado.
- **Macro con GUI:** la rama gráfica de la macro autónoma ejecutada a mano dentro de FreeCAD sigue sin probarse. El documento oculto de exportación del puente sí se ejecutó con GUI.
- **Ajuste del cierre:** el ajuste físico requiere una impresión de prueba.
- **Embobinadora:** no se tocó `coil_building` salvo para leer el encargo. No se hicieron commits ni se instaló nada.

## Archivos

- **Nuevos:** `cajas_protocolo.py`, `cajas_puente_freecad.py`, `cajas_sesion_freecad.py`, `pruebas_cajas/test_puente_protocolo.py`, `pruebas_cajas/prueba_puente_freecad.py`, `pruebas_cajas/prueba_cache_freecad.py` y este archivo.
- **Modificados:** `cajas_circuitos.py`, `cajas_freecad.py`, `README_CAJAS.md`, `pruebas_cajas/prueba_gui.py` y `pruebas_cajas/test_regresiones_cajas.py`.
- **Sin cambios:** `cajas_modelo.py`, `Cajas_para_circuitos.bat`, `prueba_freecad_real.py`, `prueba_regresiones_freecad.py` y `.gitignore`.

## Ajuste posterior: medida de la placa y holgura lateral 0 (2026-11-15, ~10:40–11:10)

Lo pidió el usuario tras usar la interfaz: los conectores laterales agrandaban la caja; quería poder personalizar las medidas, que por defecto se use el PCB sin componentes y poder dejar la placa ajustada (holgura lateral 0) con solo un aviso.

Hubo un respaldo antes de editar: `respaldo_antes_ajuste_placa_20261115_104015/`.

- **Medida de la placa** (opción nueva `board_fit`):
  - `pcb` (por defecto): planta del PCB desnudo, detectado como el sólido plano de 0,2–5 mm de mayor área, y alto total.
  - `total`: envolvente de todos los componentes.
  - `personalizado`: ancho, largo y alto escritos por el usuario, centrados en el PCB.
  En la interfaz es un selector en la pestaña Placa. Los campos se editan solo en modo personalizado. Al medir se rellena la cara inferior del PCB.
- **Colisiones:**
  - Lo que sobresale de la medida elegida y atraviesa la pared o la tapa se muestra y se avisa, nombrando las caras, en lugar de dar error.
  - Contra soportes sigue siendo error.
  - En modo `total` sigue siendo error.
  - Solo los sólidos de la placa que salen de la cavidad se intersectan con base y tapa.
- **Holgura lateral:**
  - Se acepta 0 con el aviso «placa ajustada»; por debajo de 0,2 mm avisa «muy justa».
  - El radio interior de las esquinas se reduce para no entrar en la placa (con 0 es viva).
  - La regla «radio ≤ pared + holgura» se reemplazó por pared en la diagonal de la esquina ≥ 75 % de la pared.
- **Medido con la placa del usuario** `ECDL_ADN8834_MOD_GND.step` (solo lectura):
  - PCB detectado 68 × 68 × 1,51 mm, con la cara inferior a 4,42 mm; total 77,51 × 77,51 mm.
  - Interior con holgura 0: 68 × 68 mm. Antes, con holgura 3 y la envolvente: 83,5 mm.
  - Aviso «frente, izquierda» por los conectores SMA; con aberturas en esas caras desaparece.
  - En el STEP de prueba, el PCB (44 × 59 mm) coincide con la planta total.
- **Pruebas:**
  - unittest: 74 OK.
  - `prueba_cache_freecad`: 10/10.
  - `prueba_freecad_real`: 17/17 (se cambió el caso `radius=7`, ahora válido, por `radius=8, margin=0`).
  - `prueba_regresiones_freecad`: OK.
  - `prueba_puente_freecad`: 16/16.
  - `prueba_gui`: **44/44**, con 14 comprobaciones nuevas sobre la placa ECDL: PCB por defecto, total, personalizada, holgura 0, avisos, sin relectura del STEP y captura revisada.
- **Archivos modificados:**
  - `cajas_freecad.py`, `cajas_modelo.py`, `cajas_puente_freecad.py`, `cajas_circuitos.py` y `README_CAJAS.md`;
  - las pruebas `test_cajas_modelo.py`, `prueba_freecad_real.py` y `prueba_gui.py`.

## Ajuste posterior: aberturas, unión, cierres y apoyos (2026-11-15, tarde)

Pedido del usuario: actualización automática al cambiar aberturas, formas de abertura, detección automática de aberturas con un botón, altura de la unión elegible (para insertar la placa en vertical), resolver que la placa quede flotando sobre la holgura inferior, más opciones de cierre entre base y tapa, incluidos imanes cilíndricos planos.

Hubo un respaldo antes de editar: `respaldo_antes_aberturas_cierres_20261115_175643/`.

- **Actualización automática del visor:**
  - casilla en el panel, activada y recordada en los ajustes;
  - recalcula 0,7 s después del último cambio válido, sin bloquear la interfaz (`v_auto`);
  - los errores se muestran sin diálogo.
- **Aberturas:**
  - formas rectangular, redondeada (radio), circular (Ø = ancho) y ovalada;
  - «abrir hasta la unión», con lengüeta de la tapa que cierra la ranura siguiendo la forma;
  - edición en vivo de la fila seleccionada y botón «Deseleccionar»;
  - las aberturas antiguas se leen como rectangulares.
- **Detección automática** (`cajas_freecad.detect_ports`, acción del puente `detectar`):
  - una abertura por componente que atraviesa la base o la tapa, a partir de la parte del componente que queda dentro de la pared;
  - circular solo si esa parte cabe entera en el cilindro de la abertura, y si no, redondeada con radio ≤ juego;
  - une las aberturas cercanas, lleva los bordes cercanos al piso, la unión, el techo o el canto, y no repite las que ya existen.
- **Altura de la unión** (`split_height`, 0 = automática):
  - botón «A la altura de las aberturas laterales» (mediana);
  - la validación cubre la falda frente a la lengüeta y el alojamiento de los imanes.
- **Apoyo de la placa:**
  - `board_rest` esquinas (por defecto), repisa o ninguno, hasta la cara inferior del PCB detectada, recortados por componentes;
  - `hold_down`: pisadores desde la tapa.
- **Cierres:**
  - friccion: como antes;
  - labio: continuo, con nervios cada 25 mm;
  - imanes: torres de esquina fuera de la cavidad, con alojamientos en la base y la tapa, más labio de alineación;
  - apoyada.
- **Rendimiento:**
  - las envolventes ajustadas de cada sólido se guardan en la caché de la orientación;
  - `board_collision` las usa para no lanzar booleanas innecesarias;
  - `optimalBoundingBox` se calcula sin triangulación. Con la GUI, la placa mostrada tenía una triangulación gruesa que alteraba las envolventes: 14 booleanas y 2,8 s por recálculo, en vez de 10 y 0,85 s. Además podía dar envolventes menores que las reales.
  - Recálculo con el STEP de prueba: 0,85–0,9 s en el puente y 1,4–1,6 s en la GUI (antes de este ajuste, 0,84–1,73 s).
- **Verificado con la placa del usuario** `ECDL_ADN8834_MOD_GND.step` (solo lectura):
  - se detectan 5 aberturas (4 en frente, 1 en izquierda), rectangulares redondeadas, porque los conectores tienen un cuerpo cuadrado dentro de la pared;
  - con ellas nada atraviesa la caja;
  - abiertas hasta la unión, la placa entra en vertical en la base;
  - unión a la altura de los conectores con labio: válida;
  - imanes con repisa y pisadores: válidos, revisado en capturas.
- **Pruebas:**
  - unittest: 78 OK;
  - `prueba_opciones_freecad.py` (nueva): 28 PASS;
  - `prueba_cache_freecad`: 10/10;
  - `prueba_freecad_real`: 17/17;
  - `prueba_regresiones_freecad`: OK;
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: **53/53** (9 comprobaciones nuevas: actualización automática sin bloqueo, detección, edición en vivo, formas, unión, imanes con repisa y pisadores, error sin diálogo).
- **Limitaciones:**
  - las torres de imanes sobresalen de las esquinas (unos 3 mm con imanes de 5 mm);
  - la retención real de labio, nervios e imanes requiere una impresión de prueba;
  - la detección no crea aberturas en el piso;
  - un apoyo se recorta con la envolvente de cada componente que lo toca (conservador).
- **Archivos modificados:**
  - `cajas_freecad.py`, `cajas_modelo.py`, `cajas_protocolo.py`, `cajas_puente_freecad.py`, `cajas_circuitos.py` y `README_CAJAS.md`;
  - las pruebas `test_cajas_modelo.py`, `test_puente_protocolo.py`, `prueba_freecad_real.py` y `prueba_gui.py`;
  - nueva: `pruebas_cajas/prueba_opciones_freecad.py`.

## Ajuste posterior: colores del STEP (KiCad) en el visor (2026-11-15, noche)

- **Lectura con colores.** El visor (FreeCAD con GUI) lee el STEP con `ImportGui.insert` en un documento oculto y temporal, que siempre se cierra (`cajas_freecad._read_step_colored`, `BoardCache(colors=True)`):
  - junta las piezas con su ubicación global, porque `Part.getShape(transform=True)` no incluye la de los contenedores del ensamblaje y dejaba los componentes amontonados;
  - guarda un color por cara en `info["face_colors"]`, que se aplica como `DiffuseColor` a CajaPlaca y a la placa del FCStd exportado.
  - Sigue siendo una sola lectura del STEP por archivo.
- **Sin GUI** (worker, macro) se usa `Part.read` y la placa se muestra en verde, como antes. Si el STEP no trae colores, o la reparación de geometría cambia las caras, también va en verde.
- **Coste:** la primera carga es algo más lenta (con el STEP de prueba, ~10 s frente a ~8,7 s antes; con la placa del usuario, ~3,9 s frente a 3,1 s). Los recálculos no cambian.
- **Pruebas:**
  - unittest: 78 OK;
  - `prueba_opciones_freecad`: OK;
  - `prueba_cache_freecad`: 10/10;
  - `prueba_freecad_real`: 17/17;
  - `prueba_regresiones_freecad`: OK;
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: 54/54 (nueva: «colores del STEP leídos»).
  - Capturas revisadas: la ECDL con los SMA dorados, el trimmer y los conectores; el STEP de prueba con los relés verdes y el condensador naranja.
- **Archivos modificados:** `cajas_freecad.py`, `cajas_puente_freecad.py`, `cajas_circuitos.py` y `pruebas_cajas/prueba_gui.py`.

## Ajuste posterior: aberturas de tapa sobre componentes y nueva interfaz (2026-11-15, noche)

Hubo un respaldo antes de editar: `respaldo_antes_tapa_componentes_20261115_204629/`.

- **Aberturas en la tapa (opción semiautomática):**
  - La lectura con ImportGui conserva, por sólido, el componente y su nombre de KiCad (`info["solid_groups"]`).
  - La acción del puente `componentes` devuelve, con `cajas_freecad.list_components`, para cada componente: su planta en coordenadas de la caja, si es redondo, si sale por una pared lateral y si se sugiere por su nombre.
  - El diálogo «Elegir componentes…» permite marcar y buscar.
  - `cajas_modelo.lid_ports` (Python puro) crea las aberturas: planta + juego, circular o redondeada, une las cercanas, las lleva hasta la pared y omite las ya cubiertas.
  - Sin GUI no hay nombres: cada sólido es un componente.
- **Interfaz:**
  - paleta afinada y barra superior compacta;
  - filas alineadas (etiqueta, valor, unidad, ayuda breve) y textos de ayuda más cortos (se corrigieron los desactualizados);
  - secciones plegables en Aberturas, y en Cierre solo se muestran los campos del tipo elegido;
  - visor más compacto, con «Avisos y resultado» por niveles (⚠ aviso, • información, ✗ error);
  - tablas con filas alternadas y barra de desplazamiento;
  - la barra de progreso solo aparece mientras se trabaja.
- **Pruebas:**
  - unittest: 81 OK (3 nuevas de `lid_ports`);
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: 58/58 (4 nuevas: lista con sugeridos, conectores de borde no sugeridos, aberturas creadas con recálculo, sin duplicar).
  - Las suites de FreeCAD sin ventana no se repitieron: los cambios en `cajas_freecad.py` son la lectura con GUI y la lista de componentes, que esas suites no usan.
  - Capturas revisadas: tapa de la ECDL con huecos sobre los JST, el pin header y el trimmer; capturas de la interfaz y del diálogo.

## Ajuste posterior: menú de opciones (2026-11-15, noche)

Hubo un respaldo antes de editar: `respaldo_antes_personalizacion_20261115_211611/`.

- **Menú «☰ Opciones»** a la izquierda de «Nuevo», con:
  - idioma (español / inglés): nuevo módulo `cajas_idioma.py`; se traducen en vivo los textos fijos y los principales dinámicos (estado del visor, medidas, validación correcta, título). Los mensajes de validación, de FreeCAD y de los diálogos siguen en español;
  - tema oscuro / claro: paletas `TEMAS`, aplicadas en vivo a ttk y a los widgets tk;
  - tamaño del texto: fuentes con nombre, cambian en vivo;
  - colores de base, tapa y placa en FreeCAD, y si usar los colores del STEP;
  - actualizar el visor automáticamente;
  - restablecer apariencia.
- **Colores en el protocolo:** `colores` viaja con las acciones de vista (`#RRGGBB` por pieza, `step` booleano). El puente lo guarda y lo aplica sin recalcular (`_colorear`), conservando la transparencia.
- **Pruebas:**
  - unittest: 82 OK (nueva: validación de colores);
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: 61/61 (nuevas: idioma, tema y texto de ida y vuelta, y colores en cada vista).
  - Comprobado con FreeCAD real: colores propios en base, tapa y placa; vuelta a los colores del STEP sin recalcular; color inválido rechazado.
  - Capturas revisadas: la interfaz en inglés con tema claro, y la ventana de colores.

## Documentación (2026-11-16)

- Se agregaron docstrings a unas 180 funciones y clases que no tenían, sobre todo en la interfaz.
- `README_CAJAS.md` pasó a ser `README.md` (portada del repositorio) y se reescribió con el estado actual. Las menciones a `README_CAJAS.md` en las secciones anteriores de este archivo se refieren a él.

## Ajuste posterior: cierres con tornillos (2026-11-16)

Hubo un respaldo antes de editar: `respaldo_antes_tornillos_20261116_013100/`.

- **Cierres nuevos:** `tornillos` (autorroscantes directo al plástico) e `insertos` (insertos roscados en caliente).
  - Cuatro torres de esquina fuera de la cavidad; `_corner_towers` es compartido con los imanes.
  - Tapa: paso libre y alojamiento de cabeza cilíndrica o avellanada (90°).
  - Base: agujero piloto o del inserto. También llevan labio de alineación.
- **Tabla `SCREWS`** (M2, M2.5, M3, M4), duplicada en `cajas_modelo` y `cajas_freecad`; una prueba comprueba que coincidan.
  - Agujero sin inserto = diámetro nominal (M4 → 4 mm), como pidió el usuario.
  - Con inserto: M4 → 5,4 mm (también a pedido del usuario), M3 4,0, M2.5 3,6 y M2 3,2.
  - `screw_hole` e `insert_depth` permiten cambiarlos (0 = típico).
- **`screw_fit`:** calcula el material de la tapa bajo la cabeza, la rosca útil, la profundidad del agujero y el rango de largos que sirven.
  - Rosca mínima: 2 diámetros sin inserto, o 75 % del inserto.
  - Siempre quedan al menos 0,8 mm bajo el agujero.
  - Valida en vivo y lo muestra en la interfaz.
- **Pruebas:**
  - unittest: 85 OK;
  - `prueba_opciones_freecad`: OK, con 12 comprobaciones nuevas (8 combinaciones de cierre, métrica y cabeza con agujeros y alojamientos medidos, M4 4 / 5,4 mm, y largos fuera de rango rechazados);
  - `prueba_cache_freecad`: 10/10;
  - `prueba_freecad_real`: 17/17;
  - `prueba_regresiones_freecad`: OK;
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: 61/61.
  - Captura revisada: insertos M3 con cabeza avellanada.

## Ajuste posterior: anclajes en la pared, aberturas contra el ensamble y herrajes visibles (2026-11-16)

Hubo un respaldo antes de editar: `respaldo_antes_anclajes_20261116_015251/`.

- **Imanes y tornillos dentro de las esquinas de la pared** (`anchor_place`, por defecto `pared`).
  - `anchor_wall` engruesa la pared lo justo para que el agujero y su material quepan en la esquina sin invadir la cavidad ni salir por la esquina exterior redondeada: 7,1 mm con imanes de 6 mm, 7,2 con tornillos M3 y 8,5 con insertos M4.
  - `torres` conserva el comportamiento anterior.
  - El cálculo está duplicado en `cajas_modelo.anchor_wall`, para que medidas y validación coincidan.
- **Aberturas y ensamble:**
  - Antes, una abertura que tocaba una lengüeta corta la eliminaba, y con menos de cuatro el cálculo daba error. Ahora la abertura recorta la lengüeta o el labio (y su ranura) y solo se avisa.
  - Con aberturas abiertas hasta la unión y cierres de labio, imanes o tornillos, el cálculo fallaba con «la tapa colisiona con la base durante la inserción»: el labio pasaba sobre la ranura de la abertura. Ahora sobre esas aberturas no queda labio en la base ni ranura en la tapa.
  - Reproducido y comprobado con la placa del usuario.
- **Herrajes en el visor:**
  - `hardware_shapes`: imanes, tornillos (vástago y cabeza cilíndrica o cónica) e insertos como cilindros simples, en los objetos CajaHerrajesBase y CajaHerrajesTapa (bronce los insertos, plateado el resto).
  - Siguen a su pieza en las vistas, se ocultan con la casilla «Herrajes» (`visible.hardware`) y no se exportan.
- **Pruebas:**
  - unittest: 85 OK;
  - `prueba_opciones_freecad`: OK, con nuevas comprobaciones de anclajes en la pared (no sobresalen, caben y no invaden el interior) y de aberturas hasta la unión con los cuatro cierres;
  - `prueba_freecad_real`: 17/17 (se adaptó «abertura bloquea lengüetas» al recorte);
  - `prueba_cache_freecad`: 10/10;
  - `prueba_regresiones_freecad`: OK;
  - `prueba_puente_freecad`: 16/16;
  - `prueba_gui`: 61/61.
  - Capturas revisadas: insertos y tornillos en vista explosionada, imanes en las esquinas.

# Cajas para circuitos

Aplicación de escritorio (Windows, en español) para diseñar una caja impresa en 3D a medida de una placa electrónica: **base + tapa con cierre sin tornillos**, aberturas y soportes definidos a mano, visor 3D en FreeCAD y exportación a FreeCAD (FCStd), STL y STEP, más una macro `.FCMacro` autónoma.

> Es una herramienta independiente de la embobinadora que vive en esta misma carpeta; no comparte código con ella.

## Requisitos

- Python 3.10+ con tkinter. Probado con Python 3.13. Matplotlib ya no es necesario.
- **FreeCAD 1.0** instalado. La aplicación usa `freecad.exe` como visor y `python.exe` de la misma carpeta `bin` como worker cuando el visor está cerrado. Si FreeCAD está en otro sitio, usa el botón **«Python de FreeCAD…»** y elige su `bin\python.exe` o `freecad.exe`. La ruta se guarda en `cajas_ajustes_local.json`.

No hay que instalar nada más.

La revisión posterior y sus correcciones están en `REVISION_CAJAS.md`. Mientras FreeCAD calcula se bloquean los controles de edición; siguen disponibles «Cancelar» y «Reiniciar visor». Al abrir un proyecto con STEP se vuelve a medir el archivo antes de generar, y también si cambia en disco. Las aberturas de tapa deben caber dentro del área interior para conservar su falda.

## Abrir

- Doble clic en **`Cajas_para_circuitos.bat`**, o bien
- `python cajas_circuitos.py`

Archivos de la aplicación (deben estar en la misma carpeta):

| Archivo | Qué es |
|---|---|
| `cajas_circuitos.py` | Interfaz gráfica de configuración (tkinter) con los controles del visor. |
| `cajas_sesion_freecad.py` | Cliente que arranca, vigila, reconecta y detiene el visor FreeCAD. |
| `cajas_puente_freecad.py` | Macro puente que corre **dentro** del visor FreeCAD. No se abre a mano. |
| `cajas_protocolo.py` | Protocolo JSON por archivos entre ambos lados, con acciones cerradas. |
| `cajas_modelo.py` | Configuración, validación, proyectos JSON y macros. No necesita FreeCAD. |
| `cajas_freecad.py` | Constructor geométrico, caché de la placa (`BoardCache`), sincronización del documento y *worker*. |
| `Cajas_para_circuitos.bat` | Lanzador para Windows. |
| `pruebas_cajas/` | Pruebas (ver más abajo). |

## Opciones (menú ☰ a la izquierda de «Nuevo»)

Todo se aplica al momento y se recuerda entre sesiones (en `cajas_ajustes_local.json`):

- **Idioma**: español o inglés. Se traducen los textos de la interfaz. Los mensajes de validación, los avisos de FreeCAD y los diálogos de error siguen en español.
- **Tema**: oscuro o claro.
- **Tamaño del texto**: pequeño, normal o grande.
- **Colores en FreeCAD…**: color de la base, la tapa y la placa en el visor. Una casilla decide si la placa usa los colores del STEP (KiCad) cuando los trae. Los cambios se ven en el visor sin recalcular. No afectan a los STL/STEP exportados.
- **Actualizar el visor automáticamente**: el mismo interruptor del panel del visor.
- **Restablecer apariencia**: vuelve a español, tema oscuro, texto normal y colores por defecto.

## Flujo de trabajo

1. **Placa**
   - *Medidas manuales*: ancho X, largo Y y alto Z de la **envolvente** de la placa con todos sus componentes (se modela como un bloque).
   - *STEP*: elige el `.step/.stp`, el **eje del STEP que queda vertical** (X, Y o Z) y el **giro en torno a Z**. Pulsa «Medir STEP» (o directamente «Calcular caja en FreeCAD»): FreeCAD orienta la pieza, la centra en XY, pone su punto más bajo en Z = 0 y mide la envolvente. Las medidas se actualizan antes de validar; si cambias archivo, eje o giro, se vuelve a medir.
   - **Medidas de la placa que dimensionan la caja** (con STEP):
     - *Solo el PCB, sin componentes* (por defecto): la planta de la caja es la del PCB desnudo y el alto, el de todos los componentes. El PCB se detecta como el sólido plano (0,2–5 mm de espesor) de mayor área. Lo que sobresale del PCB, como un conector lateral, puede atravesar la pared: la caja se calcula igual y se avisa en qué caras («frente, izquierda»…) para que agregues la abertura; con la abertura puesta, el aviso desaparece.
     - *Envolvente con todos los componentes*: el comportamiento anterior; nada atraviesa la pared.
     - *Personalizadas*: escribes ancho, largo y alto; se centran en el PCB.
     Si no se encuentra un PCB (por ejemplo, un STEP de un solo sólido), «Solo el PCB» usa la envolvente total y lo avisa. Al medir se muestran las dos medidas y se rellena «Cara inferior del PCB» con la altura detectada. Los proyectos antiguos, que no guardaban esta opción, se abren con «Solo el PCB».
   - El punto más bajo del STEP puede ser un pin y no el sustrato. La cara inferior del PCB se detecta y la usan los apoyos (ver «Caja»). **No se detectan agujeros de montaje automáticamente.** Las aberturas de los conectores se pueden detectar con un botón (ver «Aberturas»).
2. **Caja**: holgura lateral, inferior y superior; pared, piso, techo y radio de esquinas.
   - La **holgura lateral puede ser 0**: la placa queda ajustada contra las paredes (montaje a presión). La aplicación lo permite y solo avisa; por debajo de 0,2 mm avisa que queda muy justa. El ajuste real depende de la tolerancia de tu impresora.
   - Con poca holgura lateral, el redondeo de las esquinas interiores se reduce para no tocar la placa (con holgura 0 la esquina interior es viva). El radio exterior se rechaza solo si la pared en la diagonal de la esquina quedaría por debajo del 75 % de la pared.
   - **Apoyo de la placa** (por defecto, *apoyos en las esquinas*): bloques que suben desde el piso hasta la cara inferior del PCB detectada en el STEP, pegados a la pared y entrando «Ancho bajo el PCB» mm bajo su borde. Así la placa no flota sobre la holgura inferior ni se inclina. La *repisa perimetral* recorre todo el borde. Donde hay componentes bajo el PCB, el apoyo se recorta solo y se avisa. En modo manual apoyan la base de la envolvente.
   - **Pisadores en la tapa** (opcional): postes que bajan del techo hasta la cara superior del PCB en sus esquinas y la sujetan desde arriba. Se omiten las esquinas ocupadas por componentes.
3. **Cierre** (sin tornillos):
   - *Lengüetas cortas* biseladas (dos por cara) que entran en ranuras de la falda de la tapa, con juego por lado y nervios de fricción.
   - *Labio continuo* a presión: un labio a lo largo de cada cara (las aberturas lo recortan) con nervios cada 25 mm.
   - *Imanes*: torres en las cuatro esquinas, por fuera de la cavidad para no quitar espacio a la placa, con alojamientos para imanes cilíndricos planos en la base y en la tapa (diámetro, alto y holgura configurables; 0,2 mm más hondo que el imán), más un labio de alineación. Las torres sobresalen de la caja unos milímetros; el aviso dice cuánto. Coloca los imanes de modo que cada par se atraiga.
   - *Tapa apoyada*, sin retención.
   - **Altura de la unión**: dónde se divide la base de la tapa, medida desde la base exterior (0 = automática: techo − falda de la tapa). El botón «A la altura de las aberturas laterales» la pone a la altura de los conectores: sus aberturas quedan mitad en la base y mitad en la tapa, la placa entra en vertical en la base y la tapa cierra por encima.
4. **Aberturas** en las caras frente (−Y), fondo/trasera (+Y), izquierda (−X), derecha (+X) o tapa:
   - **Formas**: rectangular, rectangular redondeada (radio de esquinas), circular (Ø = ancho) u ovalada.
   - **Abrir hasta la unión**: la abertura sigue con su ancho hasta el plano de unión, para que el conector baje en vertical con la placa. La tapa lleva una lengüeta que baja por esa ranura y la cierra sobre el componente, siguiendo su forma.
   - **Edición en vivo**: con una fila seleccionada, lo que cambias en el formulario se aplica a esa fila (y al visor, con la actualización automática). «Deseleccionar» deja el formulario libre para agregar otra.
   - **Aberturas en la tapa sobre componentes** (sección plegable de Aberturas → «Elegir componentes…», con el visor): lista los componentes del STEP con su nombre de KiCad, posición, planta y altura. Vienen marcados los que parecen conectores, trimmers o pulsadores por su nombre (Conn, PinHeader, JST, TerminalBlock, USB, Potentiometer…), salvo los que salen por una pared lateral. Al aceptar se crea una abertura en la tapa sobre la planta de cada uno, con el juego indicado: circular si es redondo, redondeada si no. Las cercanas se unen, los bordes junto a la pared se llevan hasta ella y no se repiten las que ya existen.
   - **Detectar aberturas de componentes**: FreeCAD busca los sólidos de la placa que atraviesan la caja y agrega una abertura por componente, con el juego indicado por lado. Mide la parte del componente que queda dentro de la pared: si cabe entera en un círculo (una rosca), la abertura es circular; si no (un cuerpo cuadrado), es rectangular redondeada con radio ≤ juego, que sigue cubriendo sus esquinas. Las que quedarían a menos de 0,8 mm se unen; los bordes que quedarían a menos de 0,8 mm del piso, la unión, el techo o el canto se llevan hasta él. Los componentes que ya tienen abertura no se repiten. Opción: abrirlas hasta la unión. Nada atraviesa el piso: si un componente lo hace, se avisa.
5. **Soportes**: postes cilíndricos con agujero opcional (no perfora el piso).
6. **Calcular caja en FreeCAD**: abre el visor si hace falta. FreeCAD construye la caja exacta, comprueba las colisiones reales con la placa (STEP o envolvente), que base y tapa sean cada una un único sólido válido y que la tapa entre verticalmente, y muestra esos sólidos (ver «Visor FreeCAD»).
   - **Actualizar el visor automáticamente** (casilla del panel del visor, activada por defecto y recordada entre sesiones): con el visor abierto, cada cambio válido recalcula la caja 0,7 s después del último cambio, sin bloquear la interfaz. Si la configuración no es válida no se recalcula, y un error de FreeCAD se muestra bajo el botón, sin diálogos. Sin la casilla, la aplicación avisa de que el visor muestra una caja anterior.
7. **Exportar**
   - *Generar en carpeta nueva*: crea `<nombre>_AAAAMMDD_HHMMSS` dentro de la carpeta de salida (con sufijo `_2`, `_3`… si ya existe) con `Caja.FCStd`, `Base.stl`/`Tapa.stl` y/o `Base.step`/`Tapa.step` (orientados para imprimir: la tapa va con el techo abajo), el proyecto `.cajas.json` y la macro. Nunca se sobrescriben generaciones anteriores.
   - *Exportar macro…*: un `.FCMacro` que contiene el constructor y la configuración. En FreeCAD: **Macro → Macros…** (o arrastrar el archivo) y ejecutar. Siempre crea un documento nuevo y no guarda nada. Necesita que exista la ruta del STEP.
8. **Proyecto**: «Guardar»/«Abrir proyecto…» usan JSON (`.cajas.json`). Si el STEP está en la carpeta del proyecto o en una subcarpeta, se guarda la ruta relativa.

## Visor FreeCAD

**Colores de la placa:** el visor lee el STEP como lo hace FreeCAD al importarlo a mano, con los colores que exporta KiCad (PCB, conectores, componentes). Si el archivo no trae colores, la placa se muestra en verde. El cálculo sin visor (worker) y la macro no usan colores. El FCStd generado desde el visor conserva los colores de la placa de referencia.

El visor es una ventana de FreeCAD aparte, que la aplicación lanza y administra. No es tu FreeCAD habitual: tus documentos y preferencias no se tocan.

- **Arranque:** `freecad.exe --user-cfg <copia privada de user.cfg> cajas_puente_freecad.py`. La copia de preferencias vive en un directorio temporal de la sesión y se borra al cerrar el visor.
- **Documento:** el visor trabaja solo en su documento **«Cajas - vista previa (no guardar)»**, con los objetos CajaBase, CajaTapa, CajaPlaca y CajaParametros. Para obtener archivos, exporta desde la aplicación.
- **Placa en memoria:** el STEP se lee una sola vez (`Part.read`, unos 7–9 s con la placa de prueba).
  - Al cambiar holguras, paredes, cierre, aberturas o soportes solo se regeneran base y tapa (≈ 1–2 s) y la placa se reutiliza sin reasignarla.
  - Cambiar el eje o el giro orienta una copia de la placa en memoria, sin releer el archivo.
  - Si el archivo cambia en disco (tamaño o fecha), se vuelve a leer.
  - La caché guarda como máximo 2 archivos y 4 orientaciones.
  - El panel muestra cuántas veces se leyó el STEP en la sesión.
- **Vistas:** cerrada, separada (como se imprime) y explosionada. Se puede mostrar u ocultar la base, la tapa y la placa, y dar transparencia a la base y la tapa. Cambiar la vista solo mueve piezas, no recalcula. En la ventana de FreeCAD se gira y se acerca con el ratón como siempre.
- **Exportar:** con el visor abierto, la exportación reutiliza la placa en memoria y, si la configuración coincide, la caja ya calculada. Con el visor cerrado, se usa el worker de siempre en un proceso aparte.
- **Estado:** el panel indica si el visor está conectado, calculando, sin respuesta o cerrado. «Conectado» solo se muestra tras una respuesta real del puente.
  - Si cierras la ventana de FreeCAD, la aplicación lo detecta y «Reconectar» abre otro visor.
  - Nunca hay dos visores a la vez.
  - Si la aplicación se cierra sin cerrar el visor, este se cierra solo en unos segundos.
- **Comunicación:** archivos JSON en un directorio privado de la sesión, con token, id por petición, plazo de caducidad y un conjunto cerrado de acciones (estado, cargar placa, calcular, vista, exportar, captura y detener). No se abre ningún puerto de red y el contenido recibido nunca se ejecuta como código.

### Limitación de cancelación

Mientras FreeCAD calcula con OCC (leer un STEP o calcular booleanas), su hilo principal está ocupado: no atiende peticiones y no puede interrumpir el cálculo.

- **«Cancelar»** solo deja de esperar: la interfaz queda libre y la respuesta tardía se descarta.
- **«Reiniciar visor»** sirve para cortar de verdad un cálculo largo o un visor colgado. Termina el proceso del visor y abre otro; se pierde la placa en memoria, pero no se pierde ningún proyecto ni archivo.
- Mientras calcula, la ventana de FreeCAD no se redibuja.

### Coordenadas

- **X/Y**: origen en el centro de la caja, que coincide con el centro del PCB («Solo el PCB» o «Personalizadas») o con el de la envolvente total.
- **Z**: desde la **base exterior** (z = 0 bajo el piso). La placa ocupa z = piso + holgura inferior … + alto; la aplicación lo muestra en «Medidas y validación».
- Aberturas laterales: `u` = posición horizontal del centro a lo largo de la cara (X en frente/fondo, Y en izquierda/derecha); `v` = altura del centro desde la base exterior; ancho horizontal y alto vertical.
- Aberturas en tapa: `u` = X y `v` = Y del centro; ancho en X y alto en Y.
- Soportes: `x`, `y` desde el centro; altura desde la cara superior del piso.
- Todos los campos aceptan coma decimal (`2,5`).

## Sobre las piezas generadas

- Base y tapa son **sólidos BREP** sin historial paramétrico. Para cambiar la caja se edita el proyecto en la aplicación (o el bloque `CONFIG` de la macro) y se **regenera**. El FCStd incluye un objeto «Parametros» con la configuración en JSON y las medidas exteriores.
- El ajuste del cierre (juego e interferencia) depende de la impresora, el material y la calibración. **Imprime una prueba** (por ejemplo una caja pequeña con las mismas paredes y el mismo cierre) y ajusta el juego antes de imprimir la definitiva. No hay garantía de tolerancias físicas.
- La placa del FCStd es solo una referencia y no se exporta a STL.

## Pruebas

```bat
rem Configuración, proyectos y macros (sin FreeCAD):
python -m unittest discover -s pruebas_cajas -p "test_*.py"

rem Geometría real con el Python de FreeCAD:
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_freecad_real.py

rem Caché de placa y sincronización del documento (Python de FreeCAD, sin GUI):
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_cache_freecad.py
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_opciones_freecad.py

rem Puente con FreeCAD GUI real (abre y cierra visores propios):
python pruebas_cajas\prueba_puente_freecad.py

rem Interfaz de extremo a extremo (abre la ventana y el visor FreeCAD real):
set PYTHONIOENCODING=utf-8
python pruebas_cajas\prueba_gui.py
```

Las pruebas con visor solo terminan los procesos de FreeCAD que ellas mismas lanzaron; un FreeCAD abierto por el usuario no se toca.

Las pruebas de STEP usan `C:\Users\tupin\Downloads\TEC-8A-24V-PID-HC Step File.stp` (solo lectura) y se saltan si no existe. El estado y los resultados de la migración al visor FreeCAD están en `ESTADO_MIGRACION_FREECAD.md`.

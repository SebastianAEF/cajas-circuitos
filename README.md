# Cajas para circuitos

Aplicación de escritorio para Windows que diseña una **caja imprimible en 3D a medida de una placa electrónica**: base + tapa con **cierre sin tornillos**, aberturas para los conectores, apoyos para la placa y soportes. Usa **FreeCAD 1.0** como visor 3D y como motor geométrico, y exporta a FreeCAD (FCStd), STL y STEP, además de una macro `.FCMacro` autónoma.

La interfaz está en **español o inglés**, con tema claro u oscuro.

## Características

- **Placa desde un STEP de KiCad**, o desde medidas manuales.
  - El PCB desnudo se detecta solo y, por defecto, dimensiona la caja; también se puede usar la envolvente con todos los componentes o escribir medidas personalizadas.
  - El visor muestra la placa con los colores que exporta KiCad.
- **Visor FreeCAD en vivo**: una ventana de FreeCAD administrada por la aplicación.
  - La placa se lee una sola vez y queda en memoria; al cambiar un parámetro solo se regeneran la base y la tapa (≈ 1–2 s).
  - La actualización es automática al cambiar parámetros.
- **Aberturas**
  - Formas rectangular, redondeada, circular u ovalada.
  - **Detección automática** de los conectores que atraviesan las paredes.
  - **Aberturas en la tapa** sobre los componentes que elijas de una lista; vienen marcados los que parecen conectores, trimmers o pulsadores.
  - Opción de abrirlas **hasta la unión**, para insertar la placa en vertical; la tapa cierra la ranura.
- **Ajuste a presión**: holgura lateral desde 0 mm (la placa entra ajustada).
  - **Apoyos** bajo el PCB, en las esquinas o como repisa, para que no flote ni se incline.
  - **Pisadores** opcionales en la tapa.
- **Cierres**: lengüetas con nervios de fricción, labio continuo a presión, **imanes** cilíndricos en torres de esquina, **tornillos autorroscantes** directo al plástico, **tornillos con insertos roscados**, o tapa apoyada.
  - La **altura de la unión** entre base y tapa se puede elegir, por ejemplo a la altura de los conectores.
- **Validación real** en FreeCAD:
  - colisiones placa–caja y placa–soportes;
  - base y tapa como sólidos únicos y válidos;
  - inserción vertical de la tapa.
- **Exportación** a una carpeta nueva por generación (nunca sobrescribe), proyectos `.cajas.json` y macro autónoma.

## Requisitos

- **Windows** con **Python 3.10+** y tkinter (incluido en el instalador de python.org). Probado con Python 3.13.
- **FreeCAD 1.0** instalado. Se busca en `C:\Program Files\FreeCAD 1.0`. Si está en otro sitio, usa **«FreeCAD…»** en la barra superior y elige su `bin\python.exe` o `bin\freecad.exe`.

No hace falta instalar paquetes de Python.

## Uso

```bat
Cajas_para_circuitos.bat
```

o bien `python cajas_circuitos.py`. Todos los archivos `cajas_*.py` deben estar en la misma carpeta.

Flujo típico:

1. **Placa**: elige el STEP, el eje que queda vertical y el giro, y pulsa «Medir STEP con FreeCAD».
2. **Caja**: define las holguras (lateral 0 = a presión), las paredes y el apoyo de la placa.
3. **Cierre**: elige el tipo de cierre y, si hace falta, la altura de la unión.
4. **Aberturas**: usa «Detectar aberturas» para los conectores laterales y «Elegir componentes…» para los huecos en la tapa. También puedes agregarlas a mano.
5. **Soportes**: agrega postes si los necesitas.
6. Revisa la caja en el visor (se actualiza sola) y los avisos del panel derecho.
7. **Exportar**: «Generar en carpeta nueva» crea `Caja.FCStd`, los STL/STEP orientados para imprimir, el proyecto y la macro.

### Menú ☰ Opciones

A la izquierda de «Nuevo». Todo se aplica al momento y se recuerda entre sesiones, en `cajas_ajustes_local.json`, que es local y no se versiona.

- **Idioma**: español o inglés. Se traduce la interfaz; los mensajes de validación y los avisos de FreeCAD siguen en español.
- **Tema**: oscuro o claro.
- **Tamaño del texto**: pequeño, normal o grande.
- **Colores en FreeCAD…**: colores de la base, la tapa y la placa en el visor, y si la placa usa los colores del STEP. No afectan a los archivos exportados.
- **Actualizar el visor automáticamente** y **Restablecer apariencia**.

## Detalle de las opciones

### Placa

- **Medidas que dimensionan la caja** (con STEP):
  - *Solo el PCB* (por defecto): la planta es la del PCB desnudo y el alto, el de todos los componentes. El PCB se detecta como el sólido plano (0,2–5 mm de espesor) de mayor área. Lo que sobresale, como un conector lateral, puede atravesar la pared: se avisa en qué caras para agregar la abertura.
  - *Envolvente con todos los componentes*: nada atraviesa la pared.
  - *Personalizadas*: ancho, largo y alto escritos a mano, centrados en el PCB.
  - Si no se encuentra un PCB (por ejemplo, un STEP de un solo sólido), se usa la envolvente total y se avisa.
- La **cara inferior del PCB** se detecta al medir; la usan los apoyos y la sugerencia de altura de los soportes.

### Caja

- **Holgura lateral 0** se permite con un aviso; por debajo de 0,2 mm se avisa que queda muy justa.
- Con poca holgura, el redondeo interior de las esquinas se reduce para no tocar la placa; con 0, la esquina interior es viva.
- **Apoyos**: suben desde el piso hasta la cara inferior del PCB, pegados a la pared, y entran «Ancho bajo el PCB» mm bajo su borde. Se recortan solos donde hay componentes debajo.
- **Pisadores**: postes que bajan del techo hasta la cara superior del PCB en sus esquinas. Se omiten las esquinas ocupadas.

### Cierre

- *Lengüetas cortas*: dos por cara, biseladas, con juego por lado y nervios de fricción.
- *Labio continuo*: un labio a lo largo de cada cara, con nervios cada 25 mm. Las aberturas lo recortan.
- *Imanes*: 4 en la base y 4 en la tapa, con diámetro, alto y holgura configurables y un labio de alineación. Coloca los imanes de modo que cada par se atraiga.
- *Tornillos autorroscantes* y *tornillos con insertos roscados*: cuatro tornillos en las esquinas que pasan por la tapa y se anclan en la base. También llevan labio de alineación.
  - **Métrica** M2, M2.5, M3 o M4, y **cabeza** cilíndrica (allen, con alojamiento) o avellanada (a ras).
  - **Agujero en la base**:
    - sin inserto, el diámetro nominal (M4 → 4 mm), porque el agujero impreso sale algo menor y el tornillo hace su rosca;
    - con inserto, el del inserto (M2 3,2 · M2.5 3,6 · M3 4,0 · M4 5,4 mm), con su largo típico.
    - Ambos se pueden cambiar (0 = típico).
  - El **largo del tornillo** se valida: la aplicación indica qué largos sirven con esa caja (con rosca suficiente y sin perforar el piso).
- **Ubicación de imanes y tornillos**:
  - *Dentro de las esquinas de la pared* (por defecto): la caja queda con su exterior limpio, y la pared se engruesa automáticamente lo justo para alojarlos (por ejemplo, 7,1 mm con imanes de 6 mm). La interfaz muestra la pared que resulta.
  - *Torres por fuera de las esquinas*: la pared conserva su grosor y las torres sobresalen unos milímetros.
- *Tapa apoyada*: sin retención.
- Si una abertura cruza una lengüeta o el labio, los **recorta** (con su ranura en la tapa) y se avisa; el cálculo no se detiene. Sobre una abertura abierta hasta la unión no queda labio ni ranura, para que baje la lengüeta de la tapa que la cierra.
- **Altura de la unión** (0 = automática). «A la altura de las aberturas laterales» la coloca a la altura de los conectores: sus aberturas quedan mitad en la base y mitad en la tapa.

### Aberturas

- Caras: frente (−Y), fondo (+Y), izquierda (−X), derecha (+X) y tapa.
- **Edición en vivo**: con una fila seleccionada, lo que cambias en el formulario se aplica a ella y al visor.
- **Detectar aberturas**:
  - mide la parte de cada componente que queda dentro de la pared;
  - la abertura es circular si esa parte cabe en un círculo (una rosca) y rectangular redondeada si no;
  - une las que quedarían a menos de 0,8 mm y lleva los bordes cercanos al piso, la unión, el techo o el canto hasta ellos;
  - no crea aberturas en el piso.
- **Aberturas en la tapa sobre componentes**:
  - lista los componentes con su nombre de KiCad, posición, planta y altura;
  - premarca los que parecen conectores o ajustes (Conn, PinHeader, JST, TerminalBlock, USB, Potentiometer…), salvo los que ya salen por una pared;
  - la abertura sigue la planta del componente más el juego;
  - no se repiten las que ya existen.

### Coordenadas

- **X/Y**: origen en el centro de la caja, que coincide con el centro del PCB o con el de la envolvente. **Z**: desde la base exterior (z = 0 bajo el piso).
- Aberturas laterales:
  - `u` = posición horizontal del centro a lo largo de la cara;
  - `v` = altura del centro.
- Aberturas en la tapa: `u` = X, `v` = Y.
- Soportes: `x`, `y` desde el centro; la altura se mide desde la cara superior del piso.
- Todos los campos aceptan coma decimal (`2,5`).

## Cómo funciona el visor

El visor es una ventana de FreeCAD aparte que la aplicación lanza y administra. Tus documentos y preferencias de FreeCAD no se tocan.

- **Arranque**: `freecad.exe --user-cfg <copia privada de user.cfg> cajas_puente_freecad.py`.
- **Documento**: el visor trabaja solo en «Cajas - vista previa (no guardar)». Para obtener archivos, exporta desde la aplicación.
- **Placa en memoria**:
  - el STEP se lee una vez con el importador de FreeCAD, que conserva los colores y los nombres de los componentes;
  - un cambio de eje o giro orienta una copia, sin releer el archivo;
  - si el archivo cambia en disco, se relee;
  - la caché guarda como máximo 2 archivos y 4 orientaciones.
- **Comunicación**:
  - peticiones y respuestas JSON en un directorio privado de la sesión, con token, id y plazo de caducidad;
  - acciones cerradas: estado, cargar placa, calcular, vista, exportar, captura, detectar, componentes y detener;
  - no se abre ningún puerto de red y nada de lo recibido se ejecuta como código.
- **Herrajes**: con imanes, tornillos o insertos, el visor los muestra como cilindros simples (insertos en bronce, tornillos e imanes en plateado). Los de la tapa la acompañan en las vistas separada y explosionada. La casilla «Herrajes» de «Mostrar» los oculta. No se exportan.
- **Estado**: «Conectado» solo aparece tras una respuesta real del visor.
  - Si cierras la ventana de FreeCAD, «Reconectar» abre otro visor.
  - Nunca hay dos visores a la vez.
  - Si la aplicación se cierra, el visor se cierra solo.
- **Cancelación**: mientras FreeCAD calcula no puede interrumpirse.
  - «Cancelar» solo deja de esperar.
  - «Reiniciar» termina el visor y abre otro; se pierde la placa en memoria, no los proyectos.
- **Sin visor**: medir y exportar usan un *worker* (`cajas_freecad.py` con el Python de FreeCAD, sin ventana y sin colores).

```
cajas_circuitos.py ──(hilo)── cajas_sesion_freecad.py ──JSON por archivos── cajas_puente_freecad.py (dentro de FreeCAD)
        │                              (cajas_protocolo.py)                          │
        └── cajas_modelo.py (configuración y validación)               cajas_freecad.py (geometría y caché)
```

## Archivos

| Archivo | Qué es |
|---|---|
| `cajas_circuitos.py` | Interfaz gráfica (tkinter): formularios, menú de opciones y panel del visor. |
| `cajas_modelo.py` | Configuración, validación, proyectos JSON, aberturas de tapa y macros. No necesita FreeCAD. |
| `cajas_freecad.py` | Geometría (base, tapa, cierres, aberturas, apoyos), caché de la placa, detección de aberturas y *worker*. Se incrusta tal cual en la macro autónoma. |
| `cajas_sesion_freecad.py` | Cliente que arranca, vigila, reconecta y detiene el visor. |
| `cajas_puente_freecad.py` | Puente que corre **dentro** del visor FreeCAD. No se abre a mano. |
| `cajas_protocolo.py` | Protocolo JSON entre ambos lados, con acciones cerradas y validación. |
| `cajas_idioma.py` | Textos de la interfaz en inglés. |
| `Cajas_para_circuitos.bat` | Lanzador para Windows. |
| `pruebas_cajas/` | Pruebas (ver abajo). |
| `ESTADO_MIGRACION_FREECAD.md` | Historial de cambios, mediciones y resultados de pruebas. |

## Sobre las piezas generadas

- Base y tapa son **sólidos BREP** sin historial paramétrico. Para cambiar la caja se edita el proyecto (o `CONFIG` en la macro) y se regenera. El FCStd incluye un objeto «Parametros» con la configuración.
- El ajuste real (juego, nervios, labio, imanes, holgura 0) depende de la impresora, el material y la calibración. **Imprime una prueba** antes de la versión definitiva.
- La placa del FCStd es solo una referencia y no se exporta a STL.

## Pruebas

```bat
rem Configuración, protocolo y regresiones (sin FreeCAD):
python -m unittest discover -s pruebas_cajas -p "test_*.py"

rem Geometría con el Python de FreeCAD (sin ventana):
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_freecad_real.py
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_cache_freecad.py
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_opciones_freecad.py
"C:\Program Files\FreeCAD 1.0\bin\python.exe" pruebas_cajas\prueba_regresiones_freecad.py

rem Visor FreeCAD real (abre y cierra visores propios):
python pruebas_cajas\prueba_puente_freecad.py

rem Interfaz de extremo a extremo (ventana + visor real):
set PYTHONIOENCODING=utf-8
python pruebas_cajas\prueba_gui.py
```

- Las pruebas con visor solo terminan los procesos de FreeCAD que ellas mismas lanzaron; un FreeCAD abierto por el usuario no se toca.
- Las partes con STEP usan dos archivos locales: una placa de prueba en `Downloads` y una placa del autor en `Documents`. Se saltan si no existen; para usar otras placas, cambia las rutas al inicio de cada prueba.

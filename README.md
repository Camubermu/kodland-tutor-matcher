# Buscador de tutores — Kodland

Pegás un grupo y dice qué tutores pueden dictarlo: que enseñen ese curso, que la
franja caiga dentro de su disponibilidad declarada, y que no tengan ninguna otra
clase que se cruce. Los ordena del más libre al más cargado.

## Cómo se usa

Abrir, validar el token, pegar el **enlace del grupo** y Enter. Nada más.

Del id salen curso, día, hora, duración, zona horaria, sucursal, idioma, edades y
si el grupo ya tiene tutor — todo del API, sin interpretar el nombre.

El mismo campo acepta además, por si no tenés el enlace a mano:

- el **id suelto**: `72219`
- el **markdown completo**: `[GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)](https://bo.kodland.org/groups/72219)`
- el **nombre del grupo**: `GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)` — el curso
  se deduce mirando qué usan los otros grupos iguales, y si hay varios candidatos
  la app pregunta (ver abajo). Es el camino menos confiable de los tres.

### Enlaces al BackOffice

El nombre de cada tutor en los resultados abre su perfil
(`bo.kodland.org/teachers/<id>`) en una pestaña nueva, para verificar sin buscarlo
a mano. El título del grupo y el tutor actual también son enlaces.

### Cómo se resuelve el curso cuando solo hay el nombre

Un título como `GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)` se descompone en:
sucursal `GCC`, código `ME`, tamaño `S`, nivel `Premium`, número `69`, sábado
16:00, edades 8-9. De ahí salen sucursal, día, hora y edad con certeza.

**El curso no sale del código.** Se resuelve consultando qué curso usan de verdad
los otros grupos de la misma sucursal con el mismo código y edad
(`GET /student_groups/?business_branch=<id>`), y se ordenan por cantidad de grupos.
Si hay uno solo, se usa; si hay varios, la app los muestra con sus conteos y pide
elegir en vez de adivinar.

Esto reemplaza una tabla fija que resultó estar equivocada. Sobre una muestra de
73 grupos la relación `(código, edad) → curso` parecía una función; contra los 135
grupos reales de GCC **no lo es**:

| Código y edad | Cursos que se usan realmente |
|---|---|
| ME 8-9 | 2014 (9 grupos), 1847 (3), 2007 (1) |
| ME 10-12 | 2006 (10), 1848 (5) |
| RO 8-9 | 1845 (13), 1718 (1) |
| DC 10-12 | 2039 (21), 1603 (2) |
| DC 8-9 | 2038 (11) |
| PY 13-17 | 1844 (14) |
| RO 10-12 | 1846 (16) |

Y para `ME` el curso mayoritario ya no es el de la muestra: la tabla decía 1847 y
9 de cada 13 grupos usan 2014. El grupo 72219, usado como ejemplo durante todo el
desarrollo, es 2014 en el sistema. La tabla fija quedó solo como respaldo para
cuando no se pueden leer los grupos, y en ese caso la app avisa que puede estar
vieja.

Dos cosas que siguen siendo ciertas:

- **La duración depende del curso, no del título.** `RO` 8-9 son 60 min y `RO`
  10-12 son 90, con el mismo nombre de curso. No se deduce de `S`/`L`.
- **La edad del título no siempre coincide con la del curso.** `PY` 13-17 apunta a
  un curso declarado `[12-17]`. Por eso el filtro de edad usa solape, no igualdad.

Variantes de título que maneja: sucursales con guion (`GCC-ENG`, que hereda la
zona de GCC pero avisa que el idioma puede no ser el habitual), el prefijo `SUB_`
de los grupos de reemplazo, `IND` en lugar de `S-`/`L-` en los individuales,
sufijos de baja (`_disbanded`), horarios a la media hora (`SUN-15:30`), y títulos
donde `S`/`L` no concuerda con la edad declarada.

### Sobre GCC

GCC opera entero en `Asia/Riyadh` (UTC+3), que no cambia con el horario de verano,
así que cuando se pega solo el nombre de un grupo de GCC se asume esa zona. Para
las otras sucursales no se adivina: se pide el enlace, porque interpretar la hora
en la zona equivocada corre el resultado varias horas y sigue devolviendo
candidatos que parecen válidos.

El filtro por sucursal viene activado por defecto. Importa porque el idioma
depende de la sucursal — los grupos de GCC son en árabe — así que un tutor de otra
sucursal que "dicta el curso" no necesariamente puede dictarlo. Se puede desactivar
en *Opciones avanzadas*.

### Grupos con más de una clase por semana

`group_schedule` es una lista. Si el grupo se reúne dos veces por semana, solo se
listan tutores libres en **todas** las sesiones — quedarse con la primera
recomendaría a alguien que choca en la segunda.

## Correr

### Opción 1: el link ya desplegado

Sin instalar nada: **[abrir el buscador](https://kodland-tutor-matcher.vercel.app)**. Pegás tu
token JWT del BackOffice en la interfaz y listo. El link es privado (repo y
despliegue no son públicos); si alguien del equipo lo necesita, se le agrega
acceso — ver [Desplegado en internet](#desplegado-en-internet) más abajo.

### Opción 2: correrlo en tu máquina

No hay nada que instalar: solo librería estándar de Python 3.11+.

```bash
python3 server.py
```

Abre `http://localhost:8765`. Pegás el token JWT del BackOffice en la interfaz y listo.

`KODLAND_TOKEN='eyJ0eXAi...' python3 server.py` valida el token al arrancar
(aviso en consola) pero ya no evita tener que pegarlo una vez en la interfaz:
ver [por qué](#por-que-hay-un-servidor-y-no-solo-un-html) más abajo.

Otras banderas: `--port 9000`, `--host 0.0.0.0`, `--no-browser`, `--verbose`.

### Si falla con `CERTIFICATE_VERIFY_FAILED`

El Python de python.org en macOS no usa el llavero del sistema, así que no
encuentra los certificados raíz. Se arregla una sola vez:

```bash
/Applications/Python\ 3.x/Install\ Certificates.command    # ajustá la versión
```

o instalando `certifi`, que el código detecta y usa solo:

```bash
pip3 install certifi
```

Si tu red usa un proxy con CA propia, apuntá la variable a ese `.pem`:

```bash
KODLAND_CA_BUNDLE=/ruta/ca-corporativa.pem python3 server.py
```

Como último recurso existe `KODLAND_INSECURE=1`, que desactiva la verificación del
certificado y avisa en consola. **No lo dejes puesto:** sin verificar el
certificado, cualquiera en la red puede interceptar el token y los datos de
estudiantes que pasan por el API.

### Por qué hay un servidor y no solo un HTML

Dos razones concretas:

1. **El API solo acepta `Authorization: Bearer`.** La cookie de sesión del
   BackOffice da 401 — tener el BackOffice abierto en otra pestaña no autentica
   nada. Está verificado en `ENDPOINTS.md`.
2. Un HTML llamando al API directo desde el navegador chocaría con CORS, y
   dejaría el token expuesto en el cliente si el servidor lo reenviara con una
   clave propia. Este servidor no hace eso: es un simple puente.

**El token nunca se guarda en el servidor**, ni en memoria ni en disco: cada
pedido del navegador lo manda en el header `Authorization`, el servidor arma
un cliente nuevo para ese pedido puntual contra el API de Kodland, y no
retiene nada después de responder. El que lo guarda es el navegador
(`sessionStorage`, se borra solo al cerrar la pestaña). Esto es a propósito
para que el mismo servidor pueda atender a varias personas del equipo al
mismo tiempo sin que el token de una se mezcle con el de otra — algo
imprescindible una vez que el servidor deja de ser un proceso en tu propia
máquina y pasa a ser un link compartido (ver abajo).

### Desplegado en internet

El mismo código corre además como función serverless en Vercel (Python,
`api/[...route].py`, que reexporta el mismo `Handler` que usa el server
local — no hay lógica duplicada). El catálogo de cursos se cachea en memoria
del proceso porque es igual para cualquier token válido; nada relacionado a
un usuario particular se comparte entre pedidos.

El repositorio de GitHub y el proyecto de Vercel son **privados**: no
aparecen en buscadores ni se pueden clonar/importar sin invitación. Si
alguien del equipo necesita el código, se lo agrega como colaborador del
repo; si necesita usar el link desplegado, hay que agregarlo como miembro del
proyecto en Vercel (Project Settings → Deployment Protection) — de otra
forma Vercel le va a pedir loguearse y no va a poder entrar. El link en sí no
es un secreto que alcance por sí solo para usar el buscador: hace falta,
además, un token JWT propio y válido del BackOffice.

## Cómo decide

Dos pasos, por una razón de correctitud.

**Paso 1 — preselección.** `GET /teachers/availability_summary/?course=<id>`
devuelve una grilla día × hora con todos los tutores disponibles y su
`groups_count`. Con el filtro de curso baja de 2427 tutores a decenas, en una
sola llamada.

**Paso 2 — verificación exacta.** Por cada preseleccionado se consulta su
horario real y sus clases agendadas.

El paso 2 no es redundante: **la grilla del paso 1 asume clases de 90 minutos** y
por eso recorta el final de cada franja. Un tutor disponible hasta las 13:00 UTC
aparece en la grilla solo hasta las 11:30. Si confiáramos solo en la grilla,
perderíamos candidatos válidos para clases de 50 y 60 minutos. Por eso la grilla
acota y el horario se verifica aparte — y la ventana de preselección se ensancha
hacia atrás para recuperar a los que el recorte deja fuera.

### Filtros duros (descartan)

| Motivo | De dónde sale |
|---|---|
| No dicta el curso | filtro `?course=` de la grilla |
| Fuera de disponibilidad | bloques de `get_teacher_timetable`, en hora local del tutor |
| Cruce de horario | `get_teacher_groups_timetable` + clases extra + especiales |
| Tutor inactivo | `assignable: false` |
| Sin cupo | tope de grupos, si lo configurás en la interfaz |

Los descartados **se muestran con el motivo**. En la práctica saber *por qué*
nadie encaja es tan útil como la lista de quienes sí: los que fallan por un solo
motivo aparecen primero, porque suelen ser los accionables.

### Ranking (entre los que pasan)

Domina la **carga actual** (`groups_count`), que fue el criterio acordado: se
prefiere a quien tiene más cupo libre, para repartir. Como desempate se usa la
holgura que le queda en la agenda, para no fragmentarla con clases sueltas.

### Tres detalles que cambian resultados

- **Los flags `is_expert_lesson` / `is_extra_lesson` son aditivos, no reservas.**
  Significan "en esta franja el tutor *también* acepta clase experta o extra", y el
  bloque sigue sirviendo para un grupo regular. La primera versión los interpretó
  al revés y produjo falsos negativos graves: hay tutores con **todos** sus bloques
  marcados así, que salían como "fuera de disponibilidad" teniendo la agenda libre.
  Hoy solo se informan. Detalle de la verificación en `ENDPOINTS.md`.

- **Horario de verano.** La disponibilidad viene en hora local del tutor con su
  zona IANA. Importa incluso dentro de GCC: los grupos están en `Asia/Riyadh`
  (UTC+3 fijo) pero varios tutores están en `Africa/Cairo`, que es UTC+3 en agosto
  y UTC+2 en enero. El campo *Semana de referencia* resuelve el offset correcto
  para la semana que estás planificando.

- **Solo se buscan tutores con el curso habilitado.** Cada tutor tiene su propio
  catálogo de cursos en el BackOffice, y `availability_summary?course=<id>` respeta
  ese catálogo: quien no tenga el curso no entra a la búsqueda. Verificado contra
  datos reales — la tutora 3460750 aparece en los pools de los cursos 2038 y 2039 y
  en ningún otro, que es exactamente lo que devuelve su `get_teachers_courses`.

  Existe además un diagnóstico **apagado por defecto** en *Opciones avanzadas*:
  "mostrar también tutores sin el curso asignado". Hace una consulta extra sin el
  filtro de curso y lista a esos tutores entre los descartados con el motivo, para
  responder "¿por qué no me aparece fulano?". Si alguno debería poder dictarlo, la
  acción es asignarle el curso en el BackOffice.

## Archivos

| Archivo | Qué hace |
|---|---|
| `ENDPOINTS.md` | **Mapa del API**: cada endpoint, para qué sirve, y las trampas verificadas |
| `matching.py` | Motor de matching. Sin dependencias del API: opera sobre un modelo normalizado |
| `kodland_api.py` | Adaptador: traduce el BackOffice al modelo del motor |
| `groups.py` | Resuelve un grupo desde su enlace o su nombre |
| `server.py` | Servidor (local y desplegado) + rutas |
| `static/index.html` | Interfaz (autocontenida) |
| `api/[...route].py` | Entrypoint de Vercel: reexporta el `Handler` de `server.py` |
| `vercel.json` | Config del despliegue (directorio estático, duración de la función) |
| `test_*.py` | 140 pruebas |

Para editar cuando aparezcan casos nuevos, en `groups.py`: `BRANCH_TIMEZONES`
(zona por defecto de cada sucursal), `COURSE_CODE_HINTS` (pistas de texto) y
`GROUP_CODE_COURSE_MAP` (respaldo, solo se usa si el API de grupos falla).

El motor está separado del API a propósito: si el BackOffice cambia de forma,
se toca `kodland_api.py` y nada más.

### Pruebas

```bash
python3 -m pytest -q     # 140 pruebas, sin red
```

Cubren los puntos donde esta lógica falla en silencio: la convención de días del
API (0=domingo, no 0=lunes), conversión de zona horaria verificada contra datos
reales del BackOffice, bloques que cruzan medianoche y el límite de la semana,
clases que solo encajan parcialmente, clases contiguas vs cruzadas, horario de
verano, grupos con varias sesiones, y el ensanchado de ventana del paso 1.

`test_server.py` y `test_server_group.py` ejercitan el flujo completo contra un
API simulado que reproduce las respuestas reales, incluido el grupo 72219.

`test_regresion_falsos_negativos.py` reproduce, con datos reales del BackOffice,
los casos que aparecieron en pruebas manuales — incluido el peor: los flags
`is_expert_lesson`/`is_extra_lesson` tomados como reservas, que borraban la
disponibilidad de tutores completos.

Varias pruebas existen porque encontraron bugs reales durante el desarrollo: un
tutor con toda su disponibilidad filtrada pasaba como candidato válido; la hora de
un grupo pegado por nombre se interpretaba como UTC en vez de la zona de su
sucursal; los grupos de varias sesiones se evaluaban solo por la primera; y
`GCC-ENG` no se reconocía como sucursal.

## Límites conocidos

- **Solo lee y recomienda; no asigna.** Los endpoints de escritura existen
  (`PATCH /timetables/{id}/change_teacher/`) pero deliberadamente no se usan.
- **Tope de verificación:** por defecto se verifican a fondo los 60
  preseleccionados con menos carga. Si se recorta, la interfaz lo dice
  explícitamente en vez de fingir cobertura completa.
- **El catálogo de cursos** viene de `/courses/get_course_list/`, que pagina.
  Se pide `page_size=500`; si el catálogo real es mayor, hay que paginar de verdad.
- **`groups_count` es el conteo global** de grupos del tutor, no por curso ni por
  semana. Sirve para repartir carga, pero no distingue un grupo que termina la
  semana próxima de uno que arranca.
- Endpoints que dan **403** con el rol actual: `/courses/list_for_filters/` y
  `/teachers/available_courses/`. No se usan.

## Antes de seguir invirtiendo

**Ya existe un módulo `selector` en el API** que hace algo adyacente:
`/selector/group_selector/`, `/selector/clusters/{id}/assign/`,
`/selector/course_capacities/`, `/selector/recompute/`. Vale confirmar con el
equipo que lo mantiene si hay solapamiento con esto.

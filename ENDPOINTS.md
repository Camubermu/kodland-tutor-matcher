# Mapa de endpoints — Kodland BackOffice API v2

Base URL: `https://backoffice.kodland.org/api/v2`
Autenticación: header `Authorization: Bearer <JWT>`.

> **La cookie de sesión del BackOffice no sirve.** El API responde
> `WWW-Authenticate: Bearer` y da `401` a las peticiones con sesión de navegador.
> Tener el BackOffice abierto en otra pestaña no autentica nada: el token es
> obligatorio. Verificado en `GET /utils/server_time/`.

Todo lo que sigue fue verificado contra el API real, no leído del Swagger: el
Swagger declara mal varias respuestas (p. ej. dice que `availability_summary`
devuelve `array<Teacher>` cuando en realidad devuelve una grilla) y omite
parámetros de query que sí funcionan.

---

## Los cuatro endpoints que sostienen el matching

### 1. `GET /teachers/availability_summary/` — el atajo grande

Devuelve una grilla completa de disponibilidad de **todos** los tutores.

```json
{
  "weekdays": ["Monday", ..., "Sunday"],
  "time_slots": ["00:00", "00:05", "00:15", ...],      // 259 slots, irregulares
  "grid": {
    "Monday": {
      "00:00": {
        "count": 102,
        "teachers": [
          {"id": 537726, "full_name": "...", "team_lead": null, "groups_count": 1}
        ]
      }
    }
  }
}
```

- **`groups_count` ya trae la carga actual del tutor** — es el criterio de ranking
  que definimos, sin llamadas extra.
- **Acepta `?course=<course_id>`** aunque el Swagger no lo declare. Verificado:
  sin filtro devuelve 2427 tutores; con `?course=717` devuelve 41. Esto resuelve
  "quién dicta este curso" del lado del servidor, en una sola llamada.
- Otros filtros que expone el filterset hermano (`/teachers/get_data_for_filters/`)
  y que probablemente apliquen igual: `team_lead`, `is_active`, `business_branch`,
  `full_name`, `is_expert_lesson_teacher`.

**Dos advertencias sobre esta grilla, ambas verificadas:**

1. **La grilla está en UTC**, no en la zona del tutor ni en la del usuario.
   Comprobado con el tutor 537726 (`Asia/Singapore`, UTC+8): declara
   Tue/Thu 12:00–21:00 local y aparece en la grilla exactamente desde las 04:00;
   Wed 09:00 local → 01:00 en la grilla; Mon 08:00 local → 00:00. Los cuatro días
   coinciden al minuto con la conversión a UTC.

2. **La grilla marca dónde puede *empezar* una clase, y asume 90 minutos.**
   Ese mismo tutor declara disponibilidad hasta las 21:00 local (13:00 UTC) pero
   la grilla lo corta a las 11:30 en los cuatro días: exactamente 90 minutos
   antes. Si se confía solo en la grilla, **se pierden candidatos válidos para
   clases de 50 o 60 minutos** (el slot de 12:00 UTC es utilizable para una clase
   de 60 min y la grilla no lo lista).

   Por eso la grilla se usa para preseleccionar y la verificación fina del
   horario se hace con el endpoint 2.

### 2. `GET /teachers/{id}/get_teacher_timetable/` — disponibilidad declarada, exacta

```json
{
  "teacher": 537726,
  "timezone": "Asia/Singapore",
  "availability": [
    {"id": 21895, "start_hour": "09:00:00", "end_hour": "21:00:00",
     "weekday": "Wednesday", "groups_allowed": [],
     "is_expert_lesson": true, "is_extra_lesson": true}
  ],
  "assignable": true,
  "groups_allowed": [],
  "availability_updated_at": "2026-08-06T11:49:29+03:00",
  "availability_confirmed_at": "2026-07-23T12:42:52+03:00"
}
```

Campos que importan y que la grilla no expone:

- **`timezone`** — IANA. Necesario para convertir bien (y para manejar horario de
  verano en tutores europeos).
- **`start_hour` / `end_hour` / `weekday`** — bloques estructurados en hora local
  del tutor. Es la fuente de verdad de la disponibilidad.
- **`assignable`** — si es `false` el tutor no debe aparecer como candidato.
- **`is_expert_lesson` / `is_extra_lesson`** — son **aditivos**, no reservas.
  Significan "en esta franja el tutor *también* acepta clase experta o extra", y el
  bloque sigue sirviendo para un grupo regular.

  Esto se interpretó al revés en la primera versión, y el costo fue alto: se
  filtraban esos bloques y tutores enteros quedaban sin disponibilidad. La tutora
  3232394 tiene **todos** sus bloques marcados así, y salía como "fuera de
  disponibilidad" con la agenda libre.

  La verificación que lo zanja: la tutora 3460750 declara `Friday 13:00-17:00` con
  ambos flags en `true`, y `availability_summary` **la lista igual** en esa franja
  (comprobado en el slot `Friday 12:00` UTC). Si los flags reservaran el bloque, el
  propio API no la mostraría disponible. Hoy solo se informan.

- **`groups_allowed`** — los tipos de grupo (`group_kind`) que acepta la franja.
  Los tutores de GCC traen `[3, 8, 13, 26, 30]`, que es exactamente el
  `allowed_group_types` de la sucursal, y el grupo 72219 es del tipo 13. Se registra
  pero **no** se usa para descartar: hay tutores con la lista vacía y no está claro
  si eso significa "ninguno" o "sin configurar". Filtrar por ahí arriesgaría repetir
  el error de los flags.
- **`availability_confirmed_at`** — cuándo confirmó el tutor su disponibilidad.
  Si está vieja, la recomendación es menos confiable; vale mostrarla en la UI.

### 3. `GET /teachers/{id}/get_teacher_groups_timetable/` — los cruces reales

Lista las clases ya agendadas del tutor. **`start_time` y `end_time` vienen en
UTC absoluto** (sufijo `Z`), no en hora local ni recurrentes.

```json
[{"timetable_id": 2670106, "group_id": 64309, "group_title": "onboarding_tutors_poland",
  "group_students_count": 0, "lesson_id": 13209, "lesson_number": 21,
  "course_id": 738, "course_title": "[738] Programista gier Roblox [...]",
  "start_time": "2026-08-17T07:00:00Z", "end_time": "2026-08-17T08:30:00Z",
  "parallel_group_id": null, "parallel_group_title": null}]
```

Esta es la fuente para "que no tenga ningún otro grupo que se cruce". Como son
instancias con fecha, se proyectan a día-de-semana + hora para comparar contra un
grupo recurrente.

Complementos de la misma familia, que también ocupan la agenda del tutor y
conviene sumar para no recomendar una franja que en realidad está tomada:

- `GET /teachers/{id}/get_teacher_extra_lessons_timetable/` — clases extra
- `GET /teachers/{id}/get_teacher_special_group_lessons_timetable/` — clase cero,
  reuniones de padres, eventos
- `GET /teacher_day_offs/` — ausencias y días libres

### 4. `GET /teachers/{id}/get_teachers_courses/` — cursos del tutor

```json
[{"id": 554, "title": "[554] Scratch [2021][8-12][50m,60m][32L][Ind][Actual]",
  "has_active_groups": false}]
```

Redundante si se filtra por `?course=` en la grilla, pero sirve para mostrar en
la ficha del candidato y para el caso inverso ("¿qué más podría dictar?").

---

---

## El grupo: `GET /student_groups/{id}/get_general_info_for_group_backoffice_page/`

Este endpoint hace innecesario parsear el título del grupo. Con el id (que viene en
la URL de `bo.kodland.org/groups/<id>`) sale todo de forma autoritativa.

Verificado con el grupo **72219** (`GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)`):

```json
{
  "group_id": 72219,
  "group_title": "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)",
  "group_teacher": {"id": 245461, "full_name": "------ default teacher"},
  "group_kind": {"id": 13, "title": "[13] Mini grupo | Media, 4 estudiantes, 60 minutos"},
  "group_language": {"id": 10, "title": "Arabic"},
  "group_timezone": {"name": "Asia/Riyadh", "offset_utc": "UTC+03:00"},
  "course": {"id": 2014, "title": "[2014]Minecraft. Secret Level 1[2026][8-9][60 min][4 L][GCC][in progress]"},
  "lesson_length": 60,
  "business_branch": {"id": 12, "title": "GCC"},
  "min_age_of_students": 8, "max_age_of_students": 9, "max_students": 4,
  "group_schedule": [{"day": 6, "time": "16:00", "first_lesson_date": "2026-09-12"}],
  "all_lessons_count": 9
}
```

De acá salen los cinco datos que el motor necesita: `course.id`, `group_schedule`
(día y hora), `lesson_length`, `group_timezone` y `business_branch`.

### `group_schedule[].day` usa 0 = domingo … 6 = sábado

**No es la convención de Python** (0 = lunes). Verificado: `day: 6` con
`first_lesson_date: 2026-09-12`, que es sábado — en Python ese día es 5.
Confundirlas desplaza todo un día y no lanza ningún error: el resultado se ve
perfectamente razonable. La conversión vive en `API_DAY_TO_PYTHON` (`groups.py`)
y tiene prueba propia.

`group_schedule` es una **lista**: un grupo puede reunirse más de una vez por
semana. El tutor tiene que estar libre en todas las sesiones, no en alguna.

### El "default teacher" marca un grupo sin asignar

`group_teacher.id == 245461` (`------ default teacher`) significa que **el grupo
no tiene tutor**. Es la misma cuenta centinela que hay que excluir de la lista de
candidatos, y en `schedule_view` aparece como `teacher_id` de cada clase.

### `GET /student_groups/{id}/schedule_view/` — para validar la zona horaria

Devuelve las clases con **tiempos UTC absolutos**, lo que permite un cruce
independiente de la conversión propia:

```json
[{"timetable_id": 2980549, "timetable_time": "2026-09-12T13:00:00Z",
  "lesson_number": 1, "course_id": 2014, "teacher_id": 245461}]
```

16:00 en `Asia/Riyadh` (UTC+3) son 13:00 UTC, y es exactamente lo que dice el
sistema. `GroupResolver._crosscheck_utc` hace esta comparación en cada consulta y
avisa si no coinciden: si mañana cambia la semántica de `group_timezone`, se nota
en vez de producir recomendaciones corridas en silencio.

## Sucursales: `GET /business_branches/`

```
1=CIS  2=Indonesia  3=Turkey  4=Italy  5=Poland  6=LatAm
7=Brazil  8=test  9=ENG  10=Malaysia  11=USA  12=GCC
```

`availability_summary` acepta `business_branch`. Verificado con el curso 2014:
9 tutores sin filtro, 5 con `business_branch=12`. En el slot concreto del grupo
72219 (sábado 13:00 UTC) son 3 sin filtro y 2 con GCC.

Importa porque el idioma del grupo depende de la sucursal — los grupos de GCC son
en árabe — así que un tutor de otra sucursal que "dicta el curso" no
necesariamente puede dictarlo.

**GCC opera entero en `Asia/Riyadh` (UTC+3), que no cambia con el horario de
verano.** Por eso, cuando solo se pega el nombre del grupo (el título no declara
zona horaria), para GCC se asume Riyadh. Para las demás sucursales no se adivina:
se pide el enlace. Interpretar la hora en la zona equivocada corre el resultado
varias horas y sigue devolviendo candidatos que parecen válidos.

## Catálogo de cursos

- `GET /courses/get_course_list/` — **funciona con tu rol.** Devuelve
  `{id, title, business_branch, business_branch_title, is_active,
  number_of_lessons, course_internal_name, change_date}`.
- `GET /courses/list_for_filters/` — **403 con tu rol.**
- `GET /teachers/available_courses/` — **403 con tu rol.**

El `title` viene codificado y es parseable:

```
[554] Scratch [2021][8-12][50m,60m][32L][Ind][Actual]
 id    nombre  año   edad  duraciones  lecciones  región  estado
```

De ahí se puede sacar **la duración de la clase**, que el motor necesita y que de
otro modo habría que pedir a mano. `[50m,60m]` significa que ese curso se dicta
en bloques de 50 o 60 minutos.

## Utilidades

- `GET /utils/server_time/` → `{"utc": "2026-08-19T15:14:26+00:00"}`. Útil para
  no depender del reloj del cliente.
- `GET /utils/get_timezones_with_offsets/` → 597 zonas con
  `{name, name_with_offset, offset_utc}`.
- `GET /users/me/` → identidad del token.

## Grupos (para el flujo inverso y para poblar el formulario)

- `GET /student_groups/available_groups/` — grupos disponibles
- `GET /student_groups/matched_groups/` — grupos ya emparejados
- `GET /student_groups/{id}/schedule_view/` — horario de un grupo
- `GET /student_groups/get_data_for_filters/`

## Escritura (fuera del alcance por ahora)

Si más adelante se quiere que la herramienta **asigne** y no solo sugiera:

- `PATCH /timetables/{id}/change_teacher/` — cambia el tutor de un horario
- `PATCH /timetables/{id}/change_time/`, `PATCH /timetables/{id}/reschedule/`

Ninguno se usa en esta versión: la herramienta solo lee y recomienda.

---

## Dos cosas encontradas de paso

**Ya existe un módulo `selector` en el API** que hace algo adyacente a este
proyecto: `/selector/group_selector/`, `/selector/clusters/{id}/assign/`,
`/selector/course_capacities/`, `/selector/recompute/`. Vale revisar con el
equipo que lo mantiene si hay solapamiento antes de invertir mucho más acá.

**Hay una cuenta centinela**, id `245461` — `"------ default teacher"`, con 1044
grupos. Es un contenedor, no una persona. El motor la excluye por id; si aparecen
otras del mismo tipo hay que agregarlas a la lista de exclusión.

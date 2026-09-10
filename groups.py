"""Resolucion de un grupo a partir de su enlace o de su nombre.

Dos caminos, y no son equivalentes:

* **Por enlace o id** (`https://bo.kodland.org/groups/72219`) — el API entrega
  curso, sesiones, duracion, zona horaria y sucursal de forma autoritativa. Es el
  camino confiable y el que se usa cuando hay id.
* **Por nombre** (`GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)`) — del titulo se
  sacan con certeza la sucursal, el dia, la hora y el rango de edad. El **curso
  no**: el codigo (`ME`) es una abreviatura interna y varios cursos de la misma
  sucursal comparten franja de edad. En ese caso se devuelven los candidatos para
  que la persona elija, en lugar de adivinar.

Convencion de dias del API
--------------------------
``group_schedule[].day`` usa **0 = domingo ... 6 = sabado** (como ``getDay()`` de
JavaScript), no la de Python. Verificado con el grupo 72219: ``day: 6`` con
``first_lesson_date: 2026-09-12``, que es sabado; en Python ese dia es 5.
Confundir las dos convenciones desplaza todo un dia y no falla de forma visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from kodland_api import KodlandClient, parse_course_title
from matching import DAY_MINUTES, WEEKDAY_NAMES_ES, local_to_utc_minute_of_week

# Frontend del BackOffice (distinto del host del API). De aca salen los enlaces
# que se muestran en la interfaz: /groups/<id> y /teachers/<id>.
BO_BASE_URL = "https://bo.kodland.org"


def teacher_url(teacher_id: int | str | None) -> str | None:
    """Perfil del tutor en el BackOffice."""
    if teacher_id in (None, ""):
        return None
    return f"{BO_BASE_URL}/teachers/{teacher_id}"


def group_url(group_id: int | str | None) -> str | None:
    if group_id in (None, ""):
        return None
    return f"{BO_BASE_URL}/groups/{group_id}"


# 0=domingo..6=sabado (API)  ->  0=lunes..6=domingo (Python / este proyecto)
API_DAY_TO_PYTHON = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}

TITLE_DAY_TO_PYTHON = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}

# Verificado en GET /business_branches/
BRANCH_IDS = {
    "CIS": 1, "INDONESIA": 2, "TURKEY": 3, "ITALY": 4, "POLAND": 5,
    "LATAM": 6, "BRAZIL": 7, "TEST": 8, "ENG": 9, "MALAYSIA": 10,
    "USA": 11, "GCC": 12,
}

# Zona horaria por defecto de una sucursal, para cuando solo se pega el NOMBRE del
# grupo (el titulo no la declara). Sin esto, la hora del titulo se interpretaria
# como UTC y el resultado saldria corrido tantas horas como el offset: un error
# silencioso, porque igual devuelve candidatos que parecen razonables.
#
# GCC opera entero en Asia/Riyadh (UTC+3), que ademas no cambia con el horario de
# verano, asi que para esa sucursal el supuesto es seguro. Para las demas se
# prefiere no adivinar: se pide el enlace.
BRANCH_TIMEZONES = {
    "GCC": "Asia/Riyadh",
}

# Abreviaturas de curso vistas en titulos de grupo. Es un mapa de ayuda para la
# busqueda por texto: si el codigo no esta aca se intenta igual por coincidencia y,
# si queda ambiguo, se le pregunta a la persona.
#
# Ojo con ME: es **Minecraft Education**, no cualquier Minecraft. El catalogo de
# GCC tiene tambien "Minecraft. Secret Level 1" y "Minecraft in Scratch" para la
# misma franja de edad, y no son el curso que usan estos grupos.
COURSE_CODE_HINTS = {
    "ME": ["minecraft education", "minecraft"],
    "MC": ["minecraft"],
    "DC": ["digital creativity"],
    "RO": ["roblox"],
    "RB": ["roblox"],
    "PY": ["python"],
    "SC": ["scratch"],
    "WEB": ["web", "html"],
    "JS": ["javascript"],
    "AI": ["ai", "inteligencia", "artificial"],
    "UN": ["unity"],
}


@dataclass(frozen=True)
class CourseMapEntry:
    course_id: int
    duration_minutes: int
    name: str


# RESPALDO, no fuente de verdad. Ver `GroupResolver.course_usage`.
#
# Este mapa se derivo de una muestra de 73 grupos de GCC en la que la relacion
# (codigo, edad) -> curso parecia ser una funcion. **No lo es.** Contra los 135
# grupos de GCC del sistema, varias claves apuntan a mas de un curso:
#
#     ME 8-9    -> 2014 (9 grupos), 1847 (3), 2007 (1)
#     ME 10-12  -> 2006 (10),       1848 (5)
#     RO 8-9    -> 1845 (13),       1718 (1)
#     DC 10-12  -> 2039 (21),       1603 (2)
#     DC 8-9    -> 2038 (11)        [unico]
#     PY 13-17  -> 1844 (14)        [unico]
#     RO 10-12  -> 1846 (16)        [unico]
#
# Peor todavia: para ME el curso mayoritario hoy NO es el de la muestra. Un grupo
# "GCC ME S-... (8-9 yo)" usa 2014 nueve veces de cada trece, y este mapa decia
# 1847. El grupo 72219, que se uso como ejemplo durante todo el desarrollo, es
# 2014 en el sistema.
#
# Por eso el curso se resuelve consultando los grupos reales de la sucursal, y
# este mapa solo se usa si esa consulta falla, avisando que puede estar viejo.
#
# Lo que sigue siendo cierto y vale conservar:
#
# * **La duracion depende del curso, no del titulo.** RO 8-9 son 60 min y RO 10-12
#   son 90, con el mismo nombre de curso. No se deduce de S/L.
# * **La edad del titulo no siempre coincide con la del curso.** ME 10-12 apunta a
#   cursos declarados [10-11], y PY 13-17 a uno [12-17]. Por eso el filtro de edad
#   usa solape y no igualdad.
GROUP_CODE_COURSE_MAP: dict[tuple[str, str, str], CourseMapEntry] = {
    ("GCC", "RO", "8-9"): CourseMapEntry(1845, 60, "Roblox Game Developer"),
    ("GCC", "RO", "10-12"): CourseMapEntry(1846, 90, "Roblox Game Developer"),
    ("GCC", "PY", "13-17"): CourseMapEntry(1844, 90, "Python LVL1"),
    ("GCC", "ME", "8-9"): CourseMapEntry(
        1847, 60, "Minecraft Education: Create Your World with Code!"
    ),
    ("GCC", "ME", "10-12"): CourseMapEntry(1848, 90, "Minecraft Education LVL2"),
    ("GCC", "DC", "8-9"): CourseMapEntry(2038, 60, "Digital Creativity. Level 1"),
    ("GCC", "DC", "10-12"): CourseMapEntry(2039, 60, "Digital Creativity 2"),
}

# S y L son la franja de tamano/edad y son redundantes con "(X-Y yo)":
# en los 73 grupos reales, S siempre es 8-9 y L siempre es 10-12 o 13-17.
# Se usa solo como chequeo de consistencia del titulo.
SIZE_EXPECTED_AGES = {"S": {"8-9"}, "L": {"10-12", "13-17"}}

# Sufijos que marcan que el grupo no deberia recibir tutor.
DEAD_GROUP_SUFFIXES = ("_disbanded", "_cancelled", "_canceled", "_closed")

# Cuenta contenedor: un grupo con este tutor esta SIN ASIGNAR.
# Verificado en el grupo 72219, cuyo group_teacher es esta cuenta.
UNASSIGNED_TEACHER_ID = 245461


@dataclass
class Session:
    """Una reunion semanal del grupo, en hora local del grupo."""

    weekday: int  # 0=lunes..6=domingo
    hour: int
    minute: int

    @property
    def label(self) -> str:
        return f"{WEEKDAY_NAMES_ES[self.weekday]} {self.hour:02d}:{self.minute:02d}"


@dataclass
class GroupSpec:
    """Todo lo que hace falta para buscar tutores para un grupo."""

    source: str  # "api" | "title"
    title: str | None = None
    group_id: int | None = None
    course_id: int | None = None
    course_title: str | None = None
    course_name: str | None = None
    duration_minutes: int | None = None
    timezone: str | None = None
    sessions: list[Session] = field(default_factory=list)
    branch_id: int | None = None
    branch_title: str | None = None
    language: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    max_students: int | None = None
    group_kind_id: int | None = None
    group_kind_title: str | None = None
    current_teacher_id: int | None = None
    current_teacher_name: str | None = None
    is_archive: bool | None = None
    course_options: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def unassigned(self) -> bool | None:
        if self.current_teacher_id is None:
            return None
        return self.current_teacher_id == UNASSIGNED_TEACHER_ID

    @property
    def ready(self) -> bool:
        """True si ya se puede buscar sin mas intervencion."""
        return bool(self.course_id and self.sessions and self.duration_minutes and self.timezone)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "group_id": self.group_id,
            "course_id": self.course_id,
            "course_title": self.course_title,
            "course_name": self.course_name,
            "duration_minutes": self.duration_minutes,
            "timezone": self.timezone,
            "sessions": [
                {"weekday": s.weekday, "weekday_name": WEEKDAY_NAMES_ES[s.weekday],
                 "hour": s.hour, "minute": s.minute,
                 "start_local": f"{s.hour:02d}:{s.minute:02d}", "label": s.label}
                for s in self.sessions
            ],
            "branch_id": self.branch_id,
            "branch_title": self.branch_title,
            "language": self.language,
            "age_min": self.age_min,
            "age_max": self.age_max,
            "max_students": self.max_students,
            "group_url": group_url(self.group_id),
            "current_teacher_url": teacher_url(self.current_teacher_id),
            "group_kind_id": self.group_kind_id,
            "group_kind_title": self.group_kind_title,
            "current_teacher_id": self.current_teacher_id,
            "current_teacher_name": self.current_teacher_name,
            "unassigned": self.unassigned,
            "is_archive": self.is_archive,
            "course_options": self.course_options,
            "warnings": self.warnings,
            "ready": self.ready,
        }


# ---------------------------------------------------------------------------
# Extraccion del id
# ---------------------------------------------------------------------------

_ID_IN_URL = re.compile(r"/groups?/(\d+)")
_BARE_ID = re.compile(r"^\s*#?(\d{2,10})\s*$")


def extract_group_id(text: str | None) -> int | None:
    """Saca el id de un enlace, de un markdown ``[titulo](url)`` o de un numero."""
    if not text:
        return None
    text = text.strip()
    match = _ID_IN_URL.search(text)
    if match:
        return int(match.group(1))
    match = _BARE_ID.match(text)
    if match:
        return int(match.group(1))
    return None


def extract_title(text: str | None) -> str | None:
    """Saca el titulo de un markdown ``[titulo](url)``, o devuelve el texto tal cual."""
    if not text:
        return None
    text = text.strip()
    match = re.match(r"^\[([^\]]+)\]\(", text)
    if match:
        return match.group(1).strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return text or None


# ---------------------------------------------------------------------------
# Parseo del titulo
# ---------------------------------------------------------------------------

_DAY_TIME = re.compile(r"\(\s*([A-Za-z]{3})\s*-\s*(\d{1,2}):(\d{2})\s*\)")
_AGES = re.compile(r"\(\s*(\d{1,2})\s*-\s*(\d{1,2})\s*(?:yo|y\.o\.?|años|anos)?\s*\)", re.IGNORECASE)

# Estructura observada: "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)".
# Variantes reales: sucursal con guion ("GCC-ENG"), prefijo "SUB_" en los grupos
# de reemplazo, y "IND" en lugar de "S-"/"L-" en los individuales.
_SUB_PREFIX = re.compile(r"^\s*SUB_", re.IGNORECASE)
_TITLE_FULL = re.compile(
    r"^\s*(?P<branch>[A-Za-z]+(?:-[A-Za-z]+)*)\s+"
    r"(?P<code>[A-Za-z]{2,4})\s+"
    r"(?:(?P<size>[SLsl])-(?P<tier>[A-Za-z]+)|(?P<ind>IND))\s+"
    r"(?P<num>\d+)\b"
)
# Version laxa para titulos que no siguen la estructura completa.
_TITLE_LOOSE = re.compile(r"^\s*(?P<branch>[A-Za-z]+(?:-[A-Za-z]+)*)\s+(?P<code>[A-Za-z]{2,4})\b")


@dataclass
class TitleSpec:
    branch: str | None = None          # tal como aparece, p. ej. "GCC-ENG"
    branch_family: str | None = None   # normalizada, p. ej. "GCC"
    branch_id: int | None = None
    course_code: str | None = None
    size: str | None = None            # "S" | "L" | "IND"
    is_substitute: bool = False        # prefijo SUB_
    tier: str | None = None            # "Premium" | "Standard"
    number: int | None = None
    suffix: str | None = None          # p. ej. "_disbanded"
    sessions: list[Session] = field(default_factory=list)
    age_min: int | None = None
    age_max: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def age_key(self) -> str | None:
        if self.age_min is None or self.age_max is None:
            return None
        return f"{self.age_min}-{self.age_max}"


def parse_group_title(title: str) -> TitleSpec:
    """Descompone ``GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)``.

    Se extrae solo lo que el titulo declara. La duracion **no** esta en el titulo:
    depende del curso (RO 8-9 son 60 min y RO 10-12 son 90, con el mismo nombre).
    """
    spec = TitleSpec()
    clean = extract_title(title) or ""

    if _SUB_PREFIX.match(clean):
        spec.is_substitute = True
        clean = _SUB_PREFIX.sub("", clean, count=1)

    for dead in DEAD_GROUP_SUFFIXES:
        if clean.lower().endswith(dead):
            spec.suffix = dead
            spec.warnings.append(
                f"El titulo termina en {dead!r}: este grupo parece dado de baja y "
                "probablemente no necesite tutor."
            )
            break

    match = _TITLE_FULL.match(clean) or _TITLE_LOOSE.match(clean)
    if match:
        parts = match.groupdict()
        branch = parts["branch"].upper()
        spec.branch = branch
        # "GCC-ENG" son grupos de GCC dictados en ingles: misma sucursal, otro idioma.
        spec.branch_family = branch.split("-")[0]
        spec.course_code = parts["code"].upper()
        if parts.get("ind"):
            spec.size = "IND"
            spec.number = int(parts["num"])
        elif parts.get("size"):
            spec.size = parts["size"].upper()
            spec.tier = parts["tier"]
            spec.number = int(parts["num"])

        if branch in BRANCH_IDS:
            spec.branch_id = BRANCH_IDS[branch]
        elif spec.branch_family in BRANCH_IDS:
            spec.branch_id = BRANCH_IDS[spec.branch_family]
            spec.warnings.append(
                f"{branch} se trata como sucursal {spec.branch_family}, pero el idioma "
                "puede no ser el habitual de esa sucursal: revisa que el tutor lo hable."
            )
        else:
            spec.warnings.append(f"No reconozco la sucursal {branch!r} al inicio del titulo.")

    for match in _DAY_TIME.finditer(clean):
        day = TITLE_DAY_TO_PYTHON.get(match.group(1).upper())
        hour, minute = int(match.group(2)), int(match.group(3))
        if day is None:
            spec.warnings.append(f"No reconozco el dia {match.group(1)!r}.")
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            spec.warnings.append(f"Hora invalida en el titulo: {match.group(2)}:{match.group(3)}")
            continue
        spec.sessions.append(Session(day, hour, minute))

    if not spec.sessions:
        spec.warnings.append(
            "No encontre el dia y la hora en el titulo. Se esperaba algo como (SAT-16:00)."
        )

    # La edad se busca entre los parentesis que NO son dia-hora.
    for ages in _AGES.finditer(clean):
        if _DAY_TIME.match(ages.group(0)):
            continue
        spec.age_min, spec.age_max = int(ages.group(1)), int(ages.group(2))
        break

    # S/L es redundante con la edad; sirve para detectar titulos inconsistentes.
    if spec.size and spec.age_key:
        expected = SIZE_EXPECTED_AGES.get(spec.size)
        if expected and spec.age_key not in expected:
            spec.warnings.append(
                f"El titulo dice {spec.size}- pero la edad es {spec.age_key}; "
                f"lo esperado para {spec.size} es {' o '.join(sorted(expected))}. "
                "Verifica el grupo."
            )

    return spec


# ---------------------------------------------------------------------------
# Resolucion
# ---------------------------------------------------------------------------


class GroupResolver:
    def __init__(self, client: KodlandClient):
        self.client = client

    def resolve(self, link: str | None = None, title: str | None = None) -> GroupSpec:
        """Resuelve un grupo. Si hay id (en el enlace o en el titulo) usa el API."""
        group_id = extract_group_id(link) or extract_group_id(title)
        if group_id:
            spec = self.from_api(group_id)
            pasted = extract_title(title)
            if pasted and spec.title and _norm(pasted) != _norm(spec.title):
                spec.warnings.append(
                    f"El nombre que pegaste no coincide con el del sistema "
                    f"({spec.title!r}). Se uso el del API."
                )
            return spec

        pasted = extract_title(title) or extract_title(link)
        if not pasted:
            raise ValueError("Pega el enlace del grupo o su nombre.")
        return self.from_title(pasted)

    # -- via API ------------------------------------------------------------

    def from_api(self, group_id: int) -> GroupSpec:
        info = self.client.group_info(group_id)
        warnings: list[str] = []

        course = info.get("course") or {}
        course_id = course.get("id")
        course_title = course.get("title") or ""
        course_info = parse_course_title(course_id, course_title) if course_id else None

        tz = ((info.get("group_timezone") or {}).get("name")) or None
        if not tz:
            warnings.append("El grupo no declara zona horaria; se asume UTC.")
            tz = "UTC"

        sessions: list[Session] = []
        for entry in info.get("group_schedule") or []:
            api_day = entry.get("day")
            weekday = API_DAY_TO_PYTHON.get(api_day)
            parsed = _hhmm(entry.get("time"))
            if weekday is None or parsed is None:
                warnings.append(f"Sesion ilegible en group_schedule: {entry!r}")
                continue
            sessions.append(Session(weekday, parsed[0], parsed[1]))
        if not sessions:
            warnings.append("El grupo no tiene horario definido en group_schedule.")

        duration = info.get("lesson_length")
        if not duration and course_info:
            duration = course_info.default_duration
            warnings.append(
                f"El grupo no declara lesson_length; se tomo {duration} min del curso."
            )

        branch = info.get("business_branch") or {}
        teacher = info.get("group_teacher") or {}
        kind = info.get("group_kind") or {}

        spec = GroupSpec(
            source="api",
            group_id=info.get("group_id", group_id),
            title=info.get("group_title"),
            course_id=course_id,
            course_title=course_title,
            course_name=course_info.name if course_info else None,
            duration_minutes=int(duration) if duration else None,
            timezone=tz,
            sessions=sessions,
            branch_id=branch.get("id"),
            branch_title=branch.get("title"),
            language=(info.get("group_language") or {}).get("title"),
            age_min=info.get("min_age_of_students"),
            age_max=info.get("max_age_of_students"),
            max_students=info.get("max_students"),
            group_kind_id=kind.get("id"),
            group_kind_title=kind.get("title"),
            current_teacher_id=teacher.get("id"),
            current_teacher_name=teacher.get("full_name"),
            is_archive=info.get("group_is_archive"),
            warnings=warnings,
        )

        if spec.is_archive:
            spec.warnings.append("Este grupo esta archivado.")
        self._crosscheck_utc(spec)
        return spec

    def _crosscheck_utc(self, spec: GroupSpec) -> None:
        """Compara el horario convertido contra las clases reales del API.

        ``schedule_view`` devuelve tiempos UTC absolutos. Si la conversion de
        ``group_schedule`` + ``group_timezone`` no coincide con ellos, algo cambio
        en el API o la zona esta mal, y conviene saberlo antes de recomendar.
        """
        if not (spec.group_id and spec.sessions and spec.timezone):
            return
        try:
            lessons = self.client.group_schedule_view(spec.group_id)
        except Exception:  # noqa: BLE001 - el cruce es informativo, no critico
            return
        if not lessons:
            return

        stamps = []
        for lesson in lessons:
            parsed = _parse_iso_utc(lesson.get("timetable_time"))
            if parsed:
                stamps.append(parsed)
        if not stamps:
            return

        actual = {s.weekday() * DAY_MINUTES + s.hour * 60 + s.minute for s in stamps}
        expected = set()
        for session in spec.sessions:
            ref = stamps[0].date()
            expected.add(
                local_to_utc_minute_of_week(
                    session.weekday, session.hour, session.minute,
                    tz=spec.timezone, reference_date=ref,
                )
            )
        if not (expected & actual):
            spec.warnings.append(
                "El horario convertido no coincide con las clases reales del grupo. "
                f"Esperado en UTC: {sorted(expected)}; en el sistema: {sorted(actual)}. "
                "Revisa la zona horaria del grupo antes de confiar en el resultado."
            )

    # -- via titulo ---------------------------------------------------------

    def from_title(self, title: str) -> GroupSpec:
        parsed = parse_group_title(title)
        spec = GroupSpec(
            source="title",
            title=title,
            sessions=parsed.sessions,
            branch_id=parsed.branch_id,
            branch_title=parsed.branch,
            age_min=parsed.age_min,
            age_max=parsed.age_max,
            warnings=list(parsed.warnings),
        )

        # La zona horaria no esta en el titulo: se toma la de la sucursal. Se busca
        # por familia para que "GCC-ENG" herede la de "GCC".
        spec.timezone = BRANCH_TIMEZONES.get(parsed.branch or "") or BRANCH_TIMEZONES.get(
            parsed.branch_family or ""
        )
        if spec.timezone:
            spec.warnings.append(
                f"El titulo no declara zona horaria; se asumio {spec.timezone} "
                f"por ser la sucursal {parsed.branch}."
            )
        elif parsed.branch:
            spec.warnings.append(
                f"No se conoce la zona horaria por defecto de {parsed.branch}. "
                "Pega el enlace del grupo o elegi la zona a mano: interpretar la hora "
                "en la zona equivocada corre el resultado varias horas."
            )

        # 1) Que curso usan REALMENTE los grupos con el mismo codigo y edad.
        #    Es la unica fuente confiable: el codigo del titulo no determina el
        #    curso por si solo (ver el comentario de GROUP_CODE_COURSE_MAP).
        usage = self.course_usage(parsed)
        if usage:
            spec.course_options = usage
            if len(usage) == 1:
                elegido = usage[0]
                spec.course_id = elegido["id"]
                spec.course_name = elegido["name"]
                spec.course_title = elegido["title"]
                spec.duration_minutes = elegido.get("default_duration")
                spec.warnings.append(
                    f"Curso deducido de los {elegido['groups']} grupos de "
                    f"{parsed.branch} con codigo {parsed.course_code} y edades "
                    f"{parsed.age_key}. Confirmalo antes de asignar."
                )
            else:
                detalle = ", ".join(f"{o['name']} ({o['groups']})" for o in usage[:4])
                spec.warnings.append(
                    f"Los grupos de {parsed.branch} con codigo {parsed.course_code} y "
                    f"edades {parsed.age_key} usan mas de un curso: {detalle}. "
                    "Elegi cual, o pega el enlace del grupo para resolverlo sin dudas."
                )
            return spec

        # 2) Respaldo: el mapa fijo, que puede estar viejo.
        entry = GROUP_CODE_COURSE_MAP.get(
            (parsed.branch_family or "", parsed.course_code or "", parsed.age_key or "")
        )
        if entry:
            spec.course_id = entry.course_id
            spec.course_name = entry.name
            spec.duration_minutes = entry.duration_minutes
            spec.course_options = [{
                "id": entry.course_id, "name": entry.name, "groups": 0,
                "title": entry.name, "default_duration": entry.duration_minutes,
                "durations": [entry.duration_minutes],
                "age_range": parsed.age_key, "status": None,
            }]
            spec.warnings.append(
                "No se pudieron consultar los grupos de la sucursal, asi que el curso "
                "sale de una tabla fija que puede estar desactualizada. Verificalo con "
                "el enlace del grupo."
            )
            self._verify_mapped_course(spec, parsed)
            return spec

        if parsed.branch_id is None:
            spec.warnings.append(
                "Sin sucursal no puedo acotar el catalogo. Pega el enlace del grupo."
            )
            return spec

        # 2) Sin entrada en el mapa: se busca en el catalogo por texto y edad.
        options = self.course_options(parsed)
        spec.course_options = options

        if len(options) == 1:
            chosen = options[0]
            spec.course_id = chosen["id"]
            spec.course_title = chosen["title"]
            spec.course_name = chosen["name"]
            spec.duration_minutes = chosen.get("default_duration")
            spec.warnings.append(
                f"Curso deducido del titulo: {chosen['title']}. Confirmalo antes de asignar."
            )
        elif options:
            spec.warnings.append(
                f"{len(options)} cursos de {parsed.branch} encajan con "
                f"{parsed.course_code!r} y edades {parsed.age_min}-{parsed.age_max}. "
                "Elegi cual, o pega el enlace del grupo para resolverlo sin ambiguedad."
            )
        else:
            spec.warnings.append(
                f"Ningun curso de {parsed.branch} coincide con {parsed.course_code!r}. "
                "Pega el enlace del grupo."
            )

        # La duracion no esta en el titulo del grupo.
        if spec.duration_minutes is None and spec.course_id is None:
            spec.warnings.append(
                "El titulo no dice la duracion de la clase; revisala antes de buscar."
            )
        return spec

    def course_usage(self, parsed: TitleSpec) -> list[dict]:
        """Cursos que usan de verdad los grupos con el mismo codigo y edad.

        Se leen los grupos reales de la sucursal y se cuenta que curso tiene cada
        uno. Es preferible a cualquier tabla fija porque se actualiza solo: cuando
        GCC cambio los grupos de "ME" del curso 1847 al 2014, una tabla escrita a
        mano habria seguido devolviendo el viejo sin que nada lo delatara.

        Devuelve las opciones ordenadas por cantidad de grupos, la mas usada
        primero. Si hay mas de una, la decision queda en manos de la persona.
        """
        if parsed.branch_id is None or not parsed.course_code or not parsed.age_key:
            return []
        try:
            rows = self.client.student_groups(business_branch=parsed.branch_id)
        except Exception:  # noqa: BLE001 - se cae al respaldo
            return []

        tally: dict[int, int] = {}
        titles: dict[int, str] = {}
        for row in rows:
            course_id = row.get("course_id")
            if course_id is None:
                continue
            other = parse_group_title(row.get("title") or "")
            if other.course_code != parsed.course_code or other.age_key != parsed.age_key:
                continue
            tally[course_id] = tally.get(course_id, 0) + 1
            titles.setdefault(course_id, row.get("course_title") or "")

        options = []
        for course_id, count in tally.items():
            info = parse_course_title(course_id, titles.get(course_id, ""))
            options.append({
                "id": course_id,
                "name": info.name or f"Curso {course_id}",
                "title": info.title or f"Curso {course_id}",
                "durations": info.durations,
                "default_duration": info.default_duration,
                "age_range": info.age_range,
                "status": info.status,
                "groups": count,
            })
        options.sort(key=lambda o: (-o["groups"], o["id"]))
        return options

    def _verify_mapped_course(self, spec: GroupSpec, parsed: TitleSpec) -> None:
        """Confirma contra el catalogo que el curso del mapa sigue existiendo.

        El mapa se derivo de grupos reales, pero el catalogo cambia: si un curso se
        renombra, se archiva o cambia de duracion, conviene enterarse aca y no
        recomendando tutores para una duracion que ya no corre.
        """
        if not (parsed.branch_id and spec.course_id):
            return
        try:
            raw = self.client.courses(business_branch=parsed.branch_id, page_size=500)
        except Exception:  # noqa: BLE001 - la verificacion es informativa
            return

        for item in raw:
            if item.get("id") != spec.course_id:
                continue
            info = parse_course_title(item["id"], item.get("title", ""), item.get("is_active"))
            spec.course_title = info.title
            if info.name:
                spec.course_name = info.name
            if info.durations and spec.duration_minutes not in info.durations:
                spec.warnings.append(
                    f"El catalogo dice que el curso {spec.course_id} dura "
                    f"{'/'.join(str(d) for d in info.durations)} min, no "
                    f"{spec.duration_minutes}. Se uso el del catalogo."
                )
                spec.duration_minutes = info.default_duration
            if (info.status or "").lower() == "archive":
                spec.warnings.append(f"El curso {spec.course_id} esta archivado en el catalogo.")
            return

        spec.warnings.append(
            f"El curso {spec.course_id} (del mapa de codigos) no aparece en el catalogo "
            f"de {parsed.branch_family}. Puede haber cambiado: confirma con el enlace del grupo."
        )

    def course_options(self, parsed: TitleSpec) -> list[dict]:
        """Cursos de la sucursal que encajan con el codigo y la edad del titulo."""
        if parsed.branch_id is None:
            return []
        raw = self.client.courses(business_branch=parsed.branch_id, page_size=500)
        hints = COURSE_CODE_HINTS.get(parsed.course_code or "", [])
        needle = (parsed.course_code or "").lower()

        options = []
        for item in raw:
            course_id = item.get("id")
            if course_id is None:
                continue
            info = parse_course_title(course_id, item.get("title", ""), item.get("is_active"))
            name = (info.name or "").lower()

            matched = any(h in name for h in hints) or (len(needle) >= 3 and needle in name)
            if not matched:
                continue
            if parsed.age_min is not None and info.age_range:
                if not _age_overlaps(info.age_range, parsed.age_min, parsed.age_max):
                    continue
            if (info.status or "").lower() in {"archive"}:
                continue

            options.append({
                "id": info.course_id,
                "title": info.title,
                "name": info.name,
                "durations": info.durations,
                "default_duration": info.default_duration,
                "age_range": info.age_range,
                "status": info.status,
            })
        return options


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hhmm(value) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _parse_iso_utc(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_overlaps(age_range: str, low: int, high: int | None) -> bool:
    match = re.match(r"\s*(\d{1,2})\s*-\s*(\d{1,2})", age_range)
    if not match:
        return True
    course_low, course_high = int(match.group(1)), int(match.group(2))
    high = high if high is not None else low
    return not (course_high < low or course_low > high)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()

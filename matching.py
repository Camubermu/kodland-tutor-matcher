"""
Motor de matching tutor <-> grupo.

Este modulo es deliberadamente independiente del API de Kodland: opera sobre un
modelo normalizado. El adaptador (kodland_api.py) se encarga de traducir las
respuestas del Swagger a estas estructuras.

Modelo temporal
---------------
Las clases de Kodland son recurrentes semanales, asi que el tiempo se representa
como "minuto de la semana" en UTC: un entero en [0, 10080).

    minuto_de_semana = dia_semana * 1440 + minutos_desde_medianoche

donde dia_semana 0 = lunes ... 6 = domingo (convencion de datetime.weekday()).

Trabajar en UTC como canonico permite comparar tutores en zonas horarias
distintas sin errores, y la aritmetica circular maneja correctamente los bloques
que cruzan la medianoche o el limite de la semana (p. ej. un tutor en UTC-5 con
disponibilidad domingo 21:00 local cae en lunes 02:00 UTC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

WEEK_MINUTES = 7 * 24 * 60
DAY_MINUTES = 24 * 60

WEEKDAY_NAMES_ES = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]


# ---------------------------------------------------------------------------
# Aritmetica de intervalos sobre una semana circular
# ---------------------------------------------------------------------------


def local_to_utc_minute_of_week(
    weekday: int,
    hour: int,
    minute: int,
    tz: str | None = None,
    utc_offset_minutes: int | None = None,
    reference_date: date | None = None,
) -> int:
    """Convierte un dia/hora local al minuto de semana en UTC.

    Se acepta o un offset fijo (``utc_offset_minutes``) o un nombre de zona IANA
    (``tz``). Con ``tz`` el offset se resuelve contra ``reference_date``, de modo
    que el horario de verano queda bien resuelto para la semana en cuestion.
    """
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday fuera de rango: {weekday}")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"hora invalida: {hour}:{minute}")

    if utc_offset_minutes is None:
        if tz is None:
            raise ValueError("se requiere tz o utc_offset_minutes")
        utc_offset_minutes = resolve_offset_minutes(tz, weekday, hour, minute, reference_date)

    local_mow = weekday * DAY_MINUTES + hour * 60 + minute
    return (local_mow - utc_offset_minutes) % WEEK_MINUTES


def resolve_offset_minutes(
    tz: str,
    weekday: int,
    hour: int,
    minute: int,
    reference_date: date | None = None,
) -> int:
    """Offset UTC en minutos de ``tz`` para ese dia/hora de la semana de referencia."""
    ref = reference_date or date(2026, 1, 5)  # un lunes cualquiera si no se indica
    monday = ref - timedelta(days=ref.weekday())
    target = datetime.combine(monday + timedelta(days=weekday), time(hour, minute))
    aware = target.replace(tzinfo=ZoneInfo(tz))
    offset = aware.utcoffset()
    assert offset is not None
    return int(offset.total_seconds() // 60)


def make_intervals(start: int, duration: int) -> list[tuple[int, int]]:
    """Normaliza un bloque a una lista de intervalos ``[s, e)`` sin envolver.

    Un bloque que cruza el fin de semana se parte en dos piezas.
    """
    if duration <= 0:
        raise ValueError("la duracion debe ser positiva")
    if duration > WEEK_MINUTES:
        raise ValueError("la duracion no puede exceder una semana")

    start %= WEEK_MINUTES
    end = start + duration
    if end <= WEEK_MINUTES:
        return [(start, end)]
    return [(start, WEEK_MINUTES), (0, end - WEEK_MINUTES)]


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Une intervalos solapados o contiguos. Asume piezas ya normalizadas."""
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_covered(
    piece: tuple[int, int], covering: Sequence[tuple[int, int]]
) -> bool:
    """True si ``piece`` cabe completo dentro de algun intervalo de ``covering``."""
    start, end = piece
    return any(c_start <= start and end <= c_end for c_start, c_end in covering)


def overlap_minutes(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Minutos de solape entre dos intervalos normalizados."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def format_minute_of_week(mow: int, utc_offset_minutes: int = 0) -> str:
    """Representacion legible de un minuto de semana en la zona indicada."""
    local = (mow + utc_offset_minutes) % WEEK_MINUTES
    weekday, rest = divmod(local, DAY_MINUTES)
    hour, minute = divmod(rest, 60)
    return f"{WEEKDAY_NAMES_ES[weekday]} {hour:02d}:{minute:02d}"


# ---------------------------------------------------------------------------
# Modelo normalizado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    """Un bloque semanal recurrente, ya expresado en UTC."""

    start: int  # minuto de semana UTC
    duration: int  # minutos
    label: str = ""

    @property
    def pieces(self) -> list[tuple[int, int]]:
        return make_intervals(self.start, self.duration)

    def padded_pieces(self, buffer_minutes: int = 0) -> list[tuple[int, int]]:
        """Piezas expandidas por un colchon a cada lado (para exigir respiro)."""
        if buffer_minutes <= 0:
            return self.pieces
        return make_intervals(
            self.start - buffer_minutes, self.duration + 2 * buffer_minutes
        )


def block_from_local(
    weekday: int,
    hour: int,
    minute: int,
    duration_minutes: int,
    tz: str | None = None,
    utc_offset_minutes: int | None = None,
    reference_date: date | None = None,
    label: str = "",
) -> Block:
    """Atajo para construir un Block a partir de un dia/hora local."""
    start = local_to_utc_minute_of_week(
        weekday,
        hour,
        minute,
        tz=tz,
        utc_offset_minutes=utc_offset_minutes,
        reference_date=reference_date,
    )
    return Block(start=start, duration=duration_minutes, label=label)


@dataclass
class Tutor:
    tutor_id: str
    name: str
    courses: set[str] = field(default_factory=set)
    availability: list[Block] = field(default_factory=list)
    scheduled: list[Block] = field(default_factory=list)
    utc_offset_minutes: int = 0
    timezone: str | None = None
    max_groups: int | None = None
    active: bool = True
    reported_load: int | None = None
    availability_declared: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def current_load(self) -> int:
        """Carga actual del tutor, en numero de grupos.

        Se prefiere ``reported_load`` cuando existe: el API de Kodland reporta
        ``groups_count`` por tutor, mientras que ``scheduled`` solo trae las
        clases *proximas*, asi que contarlas subestimaria la carga real.
        """
        if self.reported_load is not None:
            return self.reported_load
        return len(self.scheduled)

    @property
    def free_slots(self) -> int | None:
        if self.max_groups is None:
            return None
        return max(0, self.max_groups - self.current_load)


@dataclass
class GroupRequest:
    """El grupo que se quiere asignar."""

    course: str
    weekday: int
    hour: int
    minute: int
    duration_minutes: int
    timezone: str | None = None
    utc_offset_minutes: int | None = None
    reference_date: date | None = None
    buffer_minutes: int = 0

    @property
    def start_utc(self) -> int:
        return local_to_utc_minute_of_week(
            self.weekday,
            self.hour,
            self.minute,
            tz=self.timezone,
            utc_offset_minutes=self.utc_offset_minutes,
            reference_date=self.reference_date,
        )

    @property
    def block(self) -> Block:
        return Block(self.start_utc, self.duration_minutes, label="grupo solicitado")


class Reason(str, Enum):
    INACTIVE = "tutor_inactivo"
    COURSE_NOT_TAUGHT = "no_dicta_el_curso"
    NOT_AVAILABLE = "fuera_de_disponibilidad"
    SCHEDULE_CONFLICT = "cruce_de_horario"
    AT_CAPACITY = "sin_cupo"


REASON_LABELS_ES = {
    Reason.INACTIVE: "Tutor inactivo",
    Reason.COURSE_NOT_TAUGHT: "No dicta este curso",
    Reason.NOT_AVAILABLE: "El horario cae fuera de su disponibilidad declarada",
    Reason.SCHEDULE_CONFLICT: "Se cruza con una clase que ya tiene asignada",
    Reason.AT_CAPACITY: "Ya alcanzo su maximo de grupos",
}


@dataclass
class Candidate:
    tutor: Tutor
    eligible: bool
    blockers: list[Reason]
    conflicts: list[str]
    score: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "tutor_id": self.tutor.tutor_id,
            "name": self.tutor.name,
            "timezone": self.tutor.timezone,
            "utc_offset_minutes": self.tutor.utc_offset_minutes,
            "eligible": self.eligible,
            "blockers": [b.value for b in self.blockers],
            "blocker_labels": [REASON_LABELS_ES[b] for b in self.blockers],
            "conflicts": self.conflicts,
            "current_load": self.tutor.current_load,
            "max_groups": self.tutor.max_groups,
            "free_slots": self.tutor.free_slots,
            "courses": sorted(self.tutor.courses),
            "score": round(self.score, 3),
            "notes": self.notes,
            "extra": self.tutor.extra,
        }


# ---------------------------------------------------------------------------
# Evaluacion
# ---------------------------------------------------------------------------


def evaluate_tutor(tutor: Tutor, request: GroupRequest) -> Candidate:
    """Aplica los filtros duros y calcula el score de un tutor para un grupo."""
    blockers: list[Reason] = []
    conflicts: list[str] = []
    notes: list[str] = []

    if not tutor.active:
        blockers.append(Reason.INACTIVE)

    if request.course not in tutor.courses:
        blockers.append(Reason.COURSE_NOT_TAUGHT)

    requested = request.block
    requested_pieces = requested.pieces

    # Disponibilidad declarada: el bloque debe quedar completamente cubierto.
    if tutor.availability:
        available = merge_intervals(
            piece for block in tutor.availability for piece in block.pieces
        )
        if not all(is_covered(piece, available) for piece in requested_pieces):
            blockers.append(Reason.NOT_AVAILABLE)
    elif tutor.availability_declared:
        # El tutor si declaro franjas, pero ninguna quedo utilizable para un grupo
        # regular (p. ej. todas reservadas para clase experta o extra). Eso es un
        # "no disponible" firme, no un "no sabemos": tratarlo como desconocido lo
        # dejaria pasar como candidato valido.
        blockers.append(Reason.NOT_AVAILABLE)
        notes.append(
            "Declara disponibilidad, pero ninguna franja queda libre para un grupo regular."
        )
    else:
        notes.append("Sin disponibilidad declarada en el sistema; no se pudo verificar.")

    # Cruce con clases ya agendadas (con colchon opcional entre clases).
    padded = requested.padded_pieces(request.buffer_minutes)
    for block in tutor.scheduled:
        for existing in block.pieces:
            if any(overlap_minutes(existing, piece) > 0 for piece in padded):
                label = block.label or format_minute_of_week(
                    block.start, tutor.utc_offset_minutes
                )
                conflicts.append(label)
                break
    if conflicts:
        blockers.append(Reason.SCHEDULE_CONFLICT)

    if tutor.max_groups is not None and tutor.current_load >= tutor.max_groups:
        blockers.append(Reason.AT_CAPACITY)

    eligible = not blockers
    return Candidate(
        tutor=tutor,
        eligible=eligible,
        blockers=blockers,
        conflicts=conflicts,
        score=score_tutor(tutor, request) if eligible else 0.0,
        notes=notes,
    )


def score_tutor(tutor: Tutor, request: GroupRequest) -> float:
    """Score de idoneidad, 0-100. Domina la carga actual (menos carga = mejor).

    Criterio acordado con el equipo: entre tutores que cumplen los filtros duros,
    se prefiere a quien tiene mas cupo libre, para repartir la carga.
    """
    # Cupo libre relativo (0-70). Sin tope declarado se asume 10 como referencia.
    cap = tutor.max_groups if tutor.max_groups else 10
    free_ratio = max(0.0, (cap - tutor.current_load) / cap)
    score = 70.0 * free_ratio

    # Holgura de disponibilidad (0-30): premia a quien conserva margen alrededor
    # del bloque, para no fragmentar su agenda con clases sueltas.
    score += 30.0 * _slack_ratio(tutor, request)
    return score


def _slack_ratio(tutor: Tutor, request: GroupRequest) -> float:
    """Fraccion del dia disponible que le queda libre al tutor ese dia."""
    if not tutor.availability:
        return 0.5

    available = merge_intervals(
        piece for block in tutor.availability for piece in block.pieces
    )
    busy = merge_intervals(
        piece for block in tutor.scheduled for piece in block.pieces
    )
    total_available = sum(end - start for start, end in available)
    if total_available == 0:
        return 0.0
    total_busy = sum(
        overlap_minutes(a, b) for a in available for b in busy
    )
    remaining = max(0, total_available - total_busy - request.duration_minutes)
    return min(1.0, remaining / total_available)


def evaluate_tutor_sessions(tutor: Tutor, requests: Sequence[GroupRequest]) -> Candidate:
    """Evalua un tutor contra *todas* las sesiones semanales del grupo.

    Un grupo puede reunirse mas de una vez por semana (``group_schedule`` del API
    es una lista). El tutor solo sirve si esta libre en **todas**: quedarse con la
    primera sesion recomendaria a alguien que choca en la segunda.
    """
    if not requests:
        raise ValueError("se requiere al menos una sesion")

    per_session = [(req, evaluate_tutor(tutor, req)) for req in requests]

    blockers: list[Reason] = []
    conflicts: list[str] = []
    notes: list[str] = []
    multi = len(requests) > 1

    for req, result in per_session:
        tag = f"{WEEKDAY_NAMES_ES[req.weekday]} {req.hour:02d}:{req.minute:02d}"
        for blocker in result.blockers:
            if blocker not in blockers:
                blockers.append(blocker)
            if multi and blocker in (Reason.NOT_AVAILABLE, Reason.SCHEDULE_CONFLICT):
                notes.append(f"{REASON_LABELS_ES[blocker]} en la sesion de {tag}.")
        for conflict in result.conflicts:
            label = f"{conflict} (sesion {tag})" if multi else conflict
            if label not in conflicts:
                conflicts.append(label)
        for note in result.notes:
            if note not in notes:
                notes.append(note)

    eligible = not blockers
    # El score se calcula sobre la sesion mas exigente (la mas larga), que es la
    # que mejor refleja el impacto real en la agenda del tutor.
    heaviest = max(requests, key=lambda r: r.duration_minutes)
    return Candidate(
        tutor=tutor,
        eligible=eligible,
        blockers=blockers,
        conflicts=conflicts,
        score=score_tutor(tutor, heaviest) if eligible else 0.0,
        notes=notes,
    )


def find_candidates(
    tutors: Iterable[Tutor],
    request: GroupRequest | Sequence[GroupRequest],
    include_rejected: bool = True,
) -> dict:
    """Evalua todos los tutores y devuelve elegibles ordenados + descartados.

    ``request`` puede ser una sesion o la lista de sesiones semanales del grupo.

    Los descartados se devuelven con el motivo explicito: en la practica saber
    *por que* nadie encaja es tan util como la lista de quienes si encajan.
    """
    requests = [request] if isinstance(request, GroupRequest) else list(request)
    if not requests:
        raise ValueError("se requiere al menos una sesion")
    primary = requests[0]

    evaluated = [evaluate_tutor_sessions(t, requests) for t in tutors]

    eligible = [c for c in evaluated if c.eligible]
    eligible.sort(
        key=lambda c: (
            -c.score,
            c.tutor.current_load,
            c.tutor.name.lower(),
        )
    )

    rejected = [c for c in evaluated if not c.eligible]
    # Los que solo fallan por un motivo son los "casi": se muestran primero
    # porque suelen ser accionables (mover el horario, ampliar el tope).
    rejected.sort(key=lambda c: (len(c.blockers), c.tutor.name.lower()))

    return {
        "request": {
            "course": primary.course,
            "weekday": primary.weekday,
            "weekday_name": WEEKDAY_NAMES_ES[primary.weekday],
            "start_local": f"{primary.hour:02d}:{primary.minute:02d}",
            "duration_minutes": primary.duration_minutes,
            "timezone": primary.timezone,
            "start_utc_minute_of_week": primary.start_utc,
            "start_utc_label": format_minute_of_week(primary.start_utc),
            "buffer_minutes": primary.buffer_minutes,
            "sessions": [
                {
                    "weekday": r.weekday,
                    "weekday_name": WEEKDAY_NAMES_ES[r.weekday],
                    "start_local": f"{r.hour:02d}:{r.minute:02d}",
                    "duration_minutes": r.duration_minutes,
                    "start_utc_minute_of_week": r.start_utc,
                    "start_utc_label": format_minute_of_week(r.start_utc),
                }
                for r in requests
            ],
        },
        "eligible": [c.as_dict() for c in eligible],
        "rejected": [c.as_dict() for c in rejected] if include_rejected else [],
        "summary": {
            "evaluated": len(evaluated),
            "eligible": len(eligible),
            "rejected": len(rejected),
        },
    }

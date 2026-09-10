"""Adaptador del BackOffice API v2 de Kodland al modelo del motor de matching.

El mapeo de campos de este archivo se verifico contra el API real (no contra el
Swagger, que declara mal varias respuestas). Ver ENDPOINTS.md para el detalle de
cada endpoint y de las trampas encontradas.

Estrategia de dos pasos, por una razon concreta:

1. ``availability_summary?course=<id>`` preselecciona del lado del servidor
   (2427 tutores -> decenas). Barato, una sola llamada.
2. Por cada preseleccionado se verifica el horario exacto con
   ``get_teacher_timetable`` y los cruces con ``get_teacher_groups_timetable``.

El paso 2 no es redundante: la grilla del paso 1 asume clases de 90 minutos y
por eso recorta el final de cada franja, perdiendo candidatos validos para
clases de 50 o 60 minutos. La grilla sirve para acotar, no para decidir.
"""

from __future__ import annotations

import json
import re
import urllib.error
import os
import ssl
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from matching import (
    DAY_MINUTES,
    WEEK_MINUTES,
    WEEKDAY_NAMES_ES,
    Block,
    Tutor,
    local_to_utc_minute_of_week,
    resolve_offset_minutes,
)

DEFAULT_BASE_URL = "https://backoffice.kodland.org/api/v2"

WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_EN_INDEX = {name: i for i, name in enumerate(WEEKDAY_EN)}

# Cuentas contenedor, no personas. Ver ENDPOINTS.md.
SENTINEL_TEACHER_IDS = {245461}

# La grilla de availability_summary asume este tamano de clase al recortar el
# final de cada franja; se usa para ensanchar la ventana de preseleccion.
GRID_ASSUMED_LESSON_MINUTES = 90


def build_ssl_context() -> ssl.SSLContext:
    """Contexto TLS que funciona tambien en macOS con Python de python.org.

    Ese Python no usa el llavero del sistema y sin certificados raiz falla con
    ``CERTIFICATE_VERIFY_FAILED``. Orden de preferencia:

    1. ``KODLAND_CA_BUNDLE`` — para redes corporativas con CA propia (proxy TLS).
    2. ``certifi``, si esta instalado: trae los certificados raiz al dia.
    3. Los certificados del sistema.

    ``KODLAND_INSECURE=1`` desactiva la verificacion. Es el ultimo recurso y avisa
    en consola: sin verificar el certificado, cualquiera en la red puede leer el
    token y los datos de estudiantes que pasan por aca. Sirve para desbloquearse
    un rato, no para dejarlo puesto.
    """
    bundle = os.environ.get("KODLAND_CA_BUNDLE", "").strip()
    if bundle:
        return ssl.create_default_context(cafile=bundle)

    if os.environ.get("KODLAND_INSECURE", "").strip() in {"1", "true", "yes"}:
        print(
            "AVISO: verificacion de certificado TLS desactivada (KODLAND_INSECURE). "
            "Usalo solo temporalmente.",
            file=sys.stderr,
        )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi es opcional
        return ssl.create_default_context()


SSL_HELP = (
    "No se pudieron verificar los certificados TLS. En macOS con Python de "
    "python.org esto se arregla corriendo una vez:\n"
    "    /Applications/Python\\ 3.x/Install\\ Certificates.command\n"
    "o instalando certifi:  pip3 install certifi\n"
    "Si tu red usa un proxy con CA propia, apunta KODLAND_CA_BUNDLE al .pem de esa CA."
)


class KodlandApiError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        self.status = status
        self.url = url
        self.body = body
        hint = ""
        if status == 401:
            hint = " — el token vencio o es invalido."
        elif status == 403:
            hint = " — tu rol no tiene permiso sobre este endpoint."
        super().__init__(f"HTTP {status} en {url}{hint} {body[:300]}")


class KodlandClient:
    """Cliente HTTP minimo. Solo stdlib, para no exigir instalaciones."""

    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 45):
        self.base_url = base_url.rstrip("/")
        self._token = token.strip()
        if self._token.lower().startswith("bearer "):
            self._token = self._token[7:].strip()
        self.timeout = timeout
        self._ssl_context = build_ssl_context()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "kodland-tutor-matcher/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise KodlandApiError(exc.code, url, exc.read().decode("utf-8", "replace")) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise KodlandApiError(0, url, f"{reason}\n\n{SSL_HELP}") from exc
            raise KodlandApiError(0, url, f"no se pudo conectar: {reason}") from exc

    # -- endpoints ----------------------------------------------------------

    def me(self) -> dict:
        return self.get("/users/me/")

    def server_time(self) -> dict:
        return self.get("/utils/server_time/")

    def availability_grid(self, course_id: int | str | None = None, **filters) -> dict:
        return self.get("/teachers/availability_summary/", {"course": course_id, **filters})

    def teacher_timetable(self, teacher_id: int) -> dict:
        return self.get(f"/teachers/{teacher_id}/get_teacher_timetable/")

    def teacher_groups_timetable(self, teacher_id: int) -> list[dict]:
        return _as_list(self.get(f"/teachers/{teacher_id}/get_teacher_groups_timetable/"))

    def teacher_extra_lessons_timetable(self, teacher_id: int) -> list[dict]:
        return _as_list(self.get(f"/teachers/{teacher_id}/get_teacher_extra_lessons_timetable/"))

    def teacher_special_lessons_timetable(self, teacher_id: int) -> list[dict]:
        return _as_list(
            self.get(f"/teachers/{teacher_id}/get_teacher_special_group_lessons_timetable/")
        )

    def teacher_courses(self, teacher_id: int) -> list[dict]:
        return _as_list(self.get(f"/teachers/{teacher_id}/get_teachers_courses/"))

    def courses(self, **params) -> list[dict]:
        return _as_list(self.get("/courses/get_course_list/", params))

    def business_branches(self) -> list[dict]:
        return _as_list(self.get("/business_branches/"))

    def group_info(self, group_id: int) -> dict:
        return self.get(f"/student_groups/{group_id}/get_general_info_for_group_backoffice_page/")

    def group_schedule_view(self, group_id: int) -> list[dict]:
        return _as_list(self.get(f"/student_groups/{group_id}/schedule_view/"))

    def student_groups(self, max_pages: int = 15, page_size: int = 100, **params) -> list[dict]:
        """Grupos de la sucursal, con ``title`` y ``course_id`` reales.

        Es la fuente para saber que curso usa de verdad cada tipo de grupo, en vez
        de deducirlo del nombre. Pagina hasta agotar o llegar a ``max_pages``.
        """
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            raw = self.get("/student_groups/", {**params, "page": page, "page_size": page_size})
            items = _as_list(raw)
            if not items:
                break
            out.extend(items)
            if isinstance(raw, list) or not (isinstance(raw, dict) and raw.get("next")):
                break
        return out


def _as_list(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "items", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
    return []


# ---------------------------------------------------------------------------
# Parseo del titulo de curso
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"(\d{2,3})\s*m", re.IGNORECASE)


@dataclass
class CourseInfo:
    course_id: int
    title: str
    name: str
    durations: list[int]
    age_range: str | None
    region: str | None
    status: str | None
    is_active: bool | None = None

    @property
    def default_duration(self) -> int:
        """Duracion mas comun del curso; 60 si el titulo no la declara."""
        return self.durations[0] if self.durations else 60


def parse_course_title(course_id: int, title: str, is_active: bool | None = None) -> CourseInfo:
    """Descompone titulos tipo ``[554] Scratch [2021][8-12][50m,60m][32L][Ind][Actual]``.

    De aqui sale la duracion de la clase, que el motor necesita para verificar
    si el bloque cabe en la disponibilidad del tutor.
    """
    brackets = re.findall(r"\[([^\]]*)\]", title or "")
    # El nombre es lo que va entre el primer corchete (el id) y el segundo.
    name = title or ""
    match = re.match(r"\s*\[[^\]]*\]\s*([^\[]*)", title or "")
    if match:
        name = match.group(1).strip() or title

    durations: list[int] = []
    age_range = region = status = None

    for token in brackets[1:]:
        token = token.strip()
        if not token:
            continue
        if _DURATION_RE.search(token) and "L" not in token.upper().replace("MIN", ""):
            found = [int(x) for x in _DURATION_RE.findall(token)]
            # Descarta valores absurdos (p. ej. "32L" no entra aca, pero por si acaso).
            durations = sorted({d for d in found if 15 <= d <= 240})
        elif re.fullmatch(r"\d{1,2}\s*-\s*\d{1,2}", token):
            age_range = token
        elif re.fullmatch(r"\d{4}", token):
            continue  # anio
        elif re.fullmatch(r"\d+\s*L", token, re.IGNORECASE):
            continue  # cantidad de lecciones
        elif token.lower() in {"actual", "archive", "in progress", "draft"}:
            status = token
        elif region is None:
            region = token

    return CourseInfo(
        course_id=course_id,
        title=title,
        name=name,
        durations=durations,
        age_range=age_range,
        region=region,
        status=status,
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# Adaptador
# ---------------------------------------------------------------------------


class KodlandAdapter:
    """Traduce el API de Kodland al modelo normalizado del motor."""

    def __init__(self, client: KodlandClient, max_workers: int = 8):
        self.client = client
        self.max_workers = max_workers

    # -- catalogo -----------------------------------------------------------

    def list_courses(self, **params) -> list[CourseInfo]:
        raw = self.client.courses(**params)
        return [
            parse_course_title(item.get("id"), item.get("title", ""), item.get("is_active"))
            for item in raw
            if item.get("id") is not None
        ]

    # -- paso 1: preseleccion via grilla -----------------------------------

    def shortlist(
        self,
        course_id: int | str | None,
        start_utc_mow: int,
        duration_minutes: int,
        widen_minutes: int | None = None,
        **filters,
    ) -> tuple[list[dict], dict]:
        """Tutores plausibles segun la grilla, mas metadatos de la consulta.

        Se toma la union de los tutores presentes en cualquier slot dentro de una
        ventana alrededor del horario pedido. La ventana se ensancha hacia atras
        porque la grilla recorta el final de cada franja asumiendo 90 minutos:
        sin ensanchar, un tutor valido para una clase de 60 min quedaria fuera.
        """
        grid_payload = self.client.availability_grid(course_id, **filters)
        grid = grid_payload.get("grid") or {}
        slots = grid_payload.get("time_slots") or []

        if widen_minutes is None:
            widen_minutes = max(0, GRID_ASSUMED_LESSON_MINUTES - duration_minutes)

        window_start = start_utc_mow - widen_minutes
        window_end = start_utc_mow + duration_minutes

        found: dict[int, dict] = {}
        slots_hit: list[str] = []

        for weekday_name, by_slot in grid.items():
            weekday = WEEKDAY_EN_INDEX.get(weekday_name)
            if weekday is None:
                continue
            for slot in slots:
                cell = by_slot.get(slot)
                if not cell or not cell.get("teachers"):
                    continue
                mow = _slot_to_mow(weekday, slot)
                if mow is None:
                    continue
                if not _within_circular(mow, window_start, window_end):
                    continue
                slots_hit.append(f"{weekday_name} {slot}")
                for stub in cell["teachers"]:
                    tid = stub.get("id")
                    if tid is None or tid in SENTINEL_TEACHER_IDS:
                        continue
                    # groups_count puede variar entre celdas; se conserva el mayor.
                    prev = found.get(tid)
                    if prev is None or (stub.get("groups_count") or 0) > (prev.get("groups_count") or 0):
                        found[tid] = stub

        meta = {
            "grid_slots_total": len(slots),
            "grid_slots_in_window": len(slots_hit),
            "window_widened_minutes": widen_minutes,
            "shortlisted": len(found),
        }
        return list(found.values()), meta

    def _tutors_in_windows(self, grid_payload: dict, windows: list[tuple[int, int]]) -> list[dict[int, dict]]:
        """Por cada ventana, los tutores que la grilla lista como disponibles."""
        grid = grid_payload.get("grid") or {}
        slots = grid_payload.get("time_slots") or []
        per_window: list[dict[int, dict]] = []

        for start, duration in windows:
            widen = max(0, GRID_ASSUMED_LESSON_MINUTES - duration)
            found: dict[int, dict] = {}
            lo, hi = start - widen, start + duration
            for weekday_name, by_slot in grid.items():
                weekday = WEEKDAY_EN_INDEX.get(weekday_name)
                if weekday is None:
                    continue
                for slot in slots:
                    cell = by_slot.get(slot)
                    if not cell or not cell.get("teachers"):
                        continue
                    mow = _slot_to_mow(weekday, slot)
                    if mow is None or not _within_circular(mow, lo, hi):
                        continue
                    for stub in cell["teachers"]:
                        tid = stub.get("id")
                        if tid is None or tid in SENTINEL_TEACHER_IDS:
                            continue
                        prev = found.get(tid)
                        if prev is None or (stub.get("groups_count") or 0) > (prev.get("groups_count") or 0):
                            found[tid] = stub
            per_window.append(found)
        return per_window

    def excluded_by_course(
        self,
        course_id: int | str,
        windows: list[tuple[int, int]],
        shortlisted_ids: set[int],
        **filters,
    ) -> list[dict]:
        """Tutores disponibles en el horario pero que no dictan el curso.

        Existe para que la interfaz pueda distinguir "no esta disponible" de "no
        tiene ese curso asignado". Sin esto, un tutor sin el curso simplemente no
        aparece en ninguna lista, y no hay forma de saber por que.
        """
        payload = self.client.availability_grid(None, **filters)
        per_window = self._tutors_in_windows(payload, windows)
        if not per_window:
            return []

        common = set(per_window[0])
        for found in per_window[1:]:
            common &= set(found)

        out = []
        for tid in common - shortlisted_ids:
            stub = next(f[tid] for f in per_window if tid in f)
            out.append(stub)
        return sorted(out, key=lambda s: (s.get("groups_count") or 0, s.get("full_name") or ""))

    def shortlist_multi(
        self,
        course_id: int | str | None,
        windows: list[tuple[int, int]],
        **filters,
    ) -> tuple[list[dict], dict]:
        """Preseleccion para un grupo con varias sesiones semanales.

        Se pide la grilla **una sola vez** y se intersecan las ventanas: el tutor
        debe aparecer plausible en todas las sesiones, no en alguna. Quedarse con
        la union recomendaria a gente que choca en la segunda clase de la semana.
        """
        if not windows:
            raise ValueError("se requiere al menos una ventana")

        grid_payload = self.client.availability_grid(course_id, **filters)
        grid = grid_payload.get("grid") or {}
        slots = grid_payload.get("time_slots") or []

        per_window: list[dict[int, dict]] = []
        widened: list[int] = []

        for start, duration in windows:
            widen = max(0, GRID_ASSUMED_LESSON_MINUTES - duration)
            widened.append(widen)
            found: dict[int, dict] = {}
            lo, hi = start - widen, start + duration

            for weekday_name, by_slot in grid.items():
                weekday = WEEKDAY_EN_INDEX.get(weekday_name)
                if weekday is None:
                    continue
                for slot in slots:
                    cell = by_slot.get(slot)
                    if not cell or not cell.get("teachers"):
                        continue
                    mow = _slot_to_mow(weekday, slot)
                    if mow is None or not _within_circular(mow, lo, hi):
                        continue
                    for stub in cell["teachers"]:
                        tid = stub.get("id")
                        if tid is None or tid in SENTINEL_TEACHER_IDS:
                            continue
                        prev = found.get(tid)
                        if prev is None or (stub.get("groups_count") or 0) > (prev.get("groups_count") or 0):
                            found[tid] = stub
            per_window.append(found)

        common_ids = set(per_window[0])
        for found in per_window[1:]:
            common_ids &= set(found)

        merged: list[dict] = []
        for tid in common_ids:
            best = max(
                (found[tid] for found in per_window if tid in found),
                key=lambda s: s.get("groups_count") or 0,
            )
            merged.append(best)

        meta = {
            "grid_slots_total": len(slots),
            "sessions": len(windows),
            "per_session_shortlist": [len(f) for f in per_window],
            "window_widened_minutes": max(widened) if widened else 0,
            "shortlisted": len(merged),
            "filters": {k: v for k, v in filters.items() if v not in (None, "")},
        }
        return merged, meta

    # -- paso 2: verificacion exacta por tutor -----------------------------

    def build_tutor(
        self,
        stub: dict,
        reference_date: date | None = None,
        include_extra_lessons: bool = True,
        include_special_lessons: bool = True,
        max_groups: int | None = None,
        course_id: int | str | None = None,
        group_kind_id: int | None = None,
    ) -> Tutor:
        """Arma un Tutor con disponibilidad y agenda reales."""
        tid = stub["id"]
        timetable = self.client.teacher_timetable(tid)
        tz = timetable.get("timezone") or "UTC"

        raw_availability = timetable.get("availability") or []
        availability: list[Block] = []
        also_expert = 0
        also_extra = 0
        kind_mismatch = 0

        for slot in raw_availability:
            weekday = WEEKDAY_EN_INDEX.get(slot.get("weekday"))
            if weekday is None:
                continue

            # OJO: is_expert_lesson e is_extra_lesson son ADITIVOS, no reservas.
            # Significan "en esta franja tambien acepta clase experta / extra", y el
            # bloque sigue sirviendo para un grupo regular. Verificado: la tutora
            # 3460750 declara viernes 13:00-17:00 con ambos flags en true y
            # availability_summary la lista igual en esa franja. Filtrar por estos
            # flags borraba la disponibilidad de tutores enteros (todos los bloques
            # de la 3232394 los tienen) y producia falsos negativos.
            if slot.get("is_expert_lesson"):
                also_expert += 1
            if slot.get("is_extra_lesson"):
                also_extra += 1

            # groups_allowed son los tipos de grupo (group_kind) que acepta la
            # franja. Se registra pero NO se descarta: una lista vacia parece
            # significar "sin configurar" y no "ninguno".
            allowed = slot.get("groups_allowed") or []
            if group_kind_id is not None and allowed and group_kind_id not in allowed:
                kind_mismatch += 1

            block = _availability_block(slot, weekday, tz, reference_date)
            if block:
                availability.append(block)

        scheduled: list[Block] = []
        sources = [("grupo", self.client.teacher_groups_timetable(tid))]
        if include_extra_lessons:
            sources.append(("clase extra", _safe(self.client.teacher_extra_lessons_timetable, tid)))
        if include_special_lessons:
            sources.append(("clase especial", _safe(self.client.teacher_special_lessons_timetable, tid)))

        for kind, entries in sources:
            for entry in entries:
                block = _scheduled_block(entry, kind)
                if block:
                    scheduled.append(block)

        return Tutor(
            tutor_id=str(tid),
            name=stub.get("full_name") or f"Tutor {tid}",
            # El filtro de curso ya lo aplico el API en availability_summary?course=,
            # asi que se marca como cumplido en lugar de volver a consultarlo por tutor.
            courses={str(course_id)} if course_id is not None else set(),
            availability=availability,
            scheduled=scheduled,
            timezone=tz,
            utc_offset_minutes=resolve_offset_minutes(tz, 0, 12, 0, reference_date),
            max_groups=max_groups,
            active=bool(timetable.get("assignable", True)),
            reported_load=stub.get("groups_count"),
            # Se distingue "no declaro nada" de "declaro, pero todo estaba
            # reservado": el motor los trata distinto y confundirlos haria pasar
            # como candidato a alguien que no lo es.
            availability_declared=bool(raw_availability),
            extra={
                "team_lead": stub.get("team_lead"),
                "groups_count": stub.get("groups_count"),
                "assignable": timetable.get("assignable"),
                "availability_confirmed_at": timetable.get("availability_confirmed_at"),
                "availability_updated_at": timetable.get("availability_updated_at"),
                "blocks_total": len(raw_availability),
                "blocks_also_expert": also_expert,
                "blocks_also_extra": also_extra,
                "blocks_kind_mismatch": kind_mismatch,
                "groups_allowed": timetable.get("groups_allowed"),
                "scheduled_count": len(scheduled),
            },
        )

    def build_tutors(self, stubs: Iterable[dict], **kwargs) -> tuple[list[Tutor], list[dict]]:
        """Construye varios tutores en paralelo. Devuelve (tutores, errores)."""
        stubs = list(stubs)
        tutors: list[Tutor] = []
        errors: list[dict] = []
        if not stubs:
            return tutors, errors

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(stubs))) as pool:
            futures = {pool.submit(self.build_tutor, s, **kwargs): s for s in stubs}
            for future, stub in futures.items():
                try:
                    tutors.append(future.result())
                except Exception as exc:  # noqa: BLE001 - se reporta al usuario
                    errors.append(
                        {
                            "tutor_id": stub.get("id"),
                            "name": stub.get("full_name"),
                            "error": str(exc)[:300],
                        }
                    )
        return tutors, errors


# ---------------------------------------------------------------------------
# Helpers de conversion
# ---------------------------------------------------------------------------


def _slot_to_mow(weekday: int, slot: str) -> int | None:
    """'14:30' + weekday -> minuto de semana (la grilla ya viene en UTC)."""
    try:
        hour, minute = (int(x) for x in slot.split(":")[:2])
    except (ValueError, AttributeError):
        return None
    return weekday * DAY_MINUTES + hour * 60 + minute


def _within_circular(mow: int, start: int, end: int) -> bool:
    """True si ``mow`` cae en [start, end] sobre la semana circular."""
    span = end - start
    if span >= WEEK_MINUTES:
        return True
    return (mow - start) % WEEK_MINUTES <= span % WEEK_MINUTES


def _parse_hhmmss(value: str) -> tuple[int, int] | None:
    try:
        parts = [int(x) for x in str(value).split(":")[:2]]
        return parts[0], parts[1]
    except (ValueError, IndexError):
        return None


def _availability_block(
    slot: dict, weekday: int, tz: str, reference_date: date | None
) -> Block | None:
    """Convierte {weekday, start_hour, end_hour} local a un Block en UTC."""
    start = _parse_hhmmss(slot.get("start_hour"))
    end = _parse_hhmmss(slot.get("end_hour"))
    if not start or not end:
        return None

    start_min = start[0] * 60 + start[1]
    end_min = end[0] * 60 + end[1]
    duration = end_min - start_min
    if duration <= 0:
        # Franja que cruza la medianoche (p. ej. 22:00-02:00).
        duration += DAY_MINUTES
    if duration <= 0:
        return None

    start_utc = local_to_utc_minute_of_week(
        weekday, start[0], start[1], tz=tz, reference_date=reference_date
    )
    label = f"disponible {slot.get('start_hour')}-{slot.get('end_hour')} ({tz})"
    return Block(start=start_utc, duration=duration, label=label)


def _scheduled_block(entry: dict, kind: str) -> Block | None:
    """Proyecta una clase con fecha absoluta (UTC) a un bloque semanal."""
    start_raw = entry.get("start_time")
    end_raw = entry.get("end_time")
    start_dt = _parse_dt(start_raw)
    if start_dt is None:
        return None

    end_dt = _parse_dt(end_raw)
    if end_dt is not None and end_dt > start_dt:
        duration = int((end_dt - start_dt).total_seconds() // 60)
    else:
        duration = 60  # el API a veces no trae end_time utilizable

    start_utc = start_dt.astimezone(timezone.utc)
    mow = start_utc.weekday() * DAY_MINUTES + start_utc.hour * 60 + start_utc.minute

    title = entry.get("group_title") or entry.get("timetable_title") or entry.get("course_title") or kind
    when = f"{WEEKDAY_NAMES_ES[start_utc.weekday()]} {start_utc:%H:%M} UTC"
    return Block(start=mow, duration=duration, label=f"{title} — {when} ({kind})")


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe(fn, *args) -> list[dict]:
    """Los endpoints complementarios pueden dar 403 segun el rol; no son criticos."""
    try:
        return fn(*args)
    except KodlandApiError:
        return []

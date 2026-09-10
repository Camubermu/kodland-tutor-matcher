#!/usr/bin/env python3
"""Servidor del buscador de tutores. Corre local o desplegado (Vercel).

El token del BackOffice NUNCA se guarda en el servidor: cada pedido del
navegador lo manda en el header ``Authorization: Bearer <token>`` y el
servidor arma un cliente nuevo por pedido. Esto es a proposito: un solo
proceso puede atender a varias personas al mismo tiempo (por ejemplo,
desplegado en internet para el equipo) sin que el token de una se mezcle
con el de otra. El navegador es quien guarda el token (en `sessionStorage`,
se borra solo al cerrar la pestaña).

Uso local:

    python3 server.py                 # abre en http://localhost:8765
    python3 server.py --port 9000 --no-browser

Con KODLAND_TOKEN en el entorno, el servidor solo valida el token al
arrancar (aviso en consola); igual hay que pegarlo una vez en la interfaz,
porque el server ya no guarda nada entre pedidos.

Solo usa la libreria estandar: no hay nada que instalar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from groups import BRANCH_IDS, GroupResolver, GroupSpec, teacher_url
from groups import Session as GroupSession
from kodland_api import (
    DEFAULT_BASE_URL,
    KodlandAdapter,
    KodlandApiError,
    KodlandClient,
    parse_course_title,
)
from matching import GroupRequest, find_candidates

STATIC_DIR = Path(__file__).parent / "static"
BASE_URL = os.environ.get("KODLAND_BASE_URL", DEFAULT_BASE_URL)

# Catalogo de cursos: no es informacion de un usuario particular (es el mismo
# catalogo para cualquier token valido), asi que se puede compartir entre
# pedidos de distintas personas sin riesgo. Con lock porque varios pedidos
# concurrentes pueden pisarse la carga inicial.
_courses_cache_lock = threading.Lock()
_courses_cache: list[dict] | None = None


# ---------------------------------------------------------------------------
# Logica de las rutas
# ---------------------------------------------------------------------------


def handle_auth(payload: dict) -> dict:
    """Solo valida el token contra el API y devuelve quien es. No lo guarda:
    el que lo guarda es el navegador, que lo va a mandar en cada pedido
    siguiente."""
    token = (payload.get("token") or "").strip()
    if not token:
        raise ValueError("No llego ningun token.")
    user = KodlandClient(token, BASE_URL).me()  # falla temprano si el token no sirve
    return {
        "ok": True,
        "user": {
            "id": user.get("id"),
            "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            "email": user.get("email"),
        },
    }


def handle_courses(client: KodlandClient, query: dict) -> dict:
    global _courses_cache
    with _courses_cache_lock:
        if _courses_cache is None:
            # El endpoint pagina; se pide un tope alto para tener el catalogo completo.
            raw = client.courses(page_size=500)
            infos = [
                parse_course_title(item.get("id"), item.get("title", ""), item.get("is_active"))
                for item in raw
                if item.get("id") is not None
            ]
            _courses_cache = [
                {
                    "id": c.course_id,
                    "title": c.title,
                    "name": c.name,
                    "durations": c.durations,
                    "default_duration": c.default_duration,
                    "age_range": c.age_range,
                    "region": c.region,
                    "status": c.status,
                    "is_active": c.is_active,
                }
                for c in infos
            ]
        cache = _courses_cache

    courses = cache
    search = (query.get("search") or [""])[0].strip().lower()
    if search:
        courses = [
            c
            for c in courses
            if search in (c["title"] or "").lower() or search == str(c["id"])
        ]
    return {"courses": courses, "total": len(cache)}


def handle_group(client: KodlandClient, payload: dict) -> dict:
    """Resuelve un grupo desde su enlace o su nombre, sin buscar tutores todavia."""
    link = (payload.get("link") or "").strip()
    title = (payload.get("title") or "").strip()
    if not link and not title:
        raise ValueError("Pega el enlace del grupo o su nombre.")
    spec = GroupResolver(client).resolve(link=link or None, title=title or None)
    return {"group": spec.as_dict()}


def _sessions_from_payload(payload: dict) -> list[GroupSession]:
    """Sesiones explicitas del payload (una o varias)."""
    raw = payload.get("sessions")
    if raw:
        out = []
        for entry in raw:
            weekday = int(entry.get("weekday", 0))
            hour, minute = _parse_hhmm(entry.get("start_time") or "09:00")
            out.append(GroupSession(weekday, hour, minute))
        return out

    weekday = int(payload.get("weekday", 0))
    hour, minute = _parse_hhmm(payload.get("start_time") or "09:00")
    return [GroupSession(weekday, hour, minute)]


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        parts = str(value).split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Hora invalida: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Hora invalida: {value!r}")
    return hour, minute


def handle_search(client: KodlandClient, payload: dict) -> dict:
    """Busca tutores. Acepta un grupo (enlace/nombre) o parametros manuales."""
    group: GroupSpec | None = None
    if payload.get("link") or payload.get("title"):
        group = GroupResolver(client).resolve(
            link=(payload.get("link") or "").strip() or None,
            title=(payload.get("title") or "").strip() or None,
        )
        if not group.course_id and not payload.get("course_id"):
            # No se pudo determinar el curso: se devuelve el grupo con sus opciones
            # en lugar de buscar con un curso inventado.
            return {"needs_input": True, "group": group.as_dict()}

    course_id = payload.get("course_id") or (group.course_id if group else None)
    if course_id in (None, ""):
        raise ValueError("Falta el curso.")

    if group and group.sessions and not payload.get("sessions") and not payload.get("weekday_override"):
        sessions = group.sessions
    else:
        sessions = _sessions_from_payload(payload)
    for session in sessions:
        if not 0 <= session.weekday <= 6:
            raise ValueError("Dia de la semana invalido.")

    duration = payload.get("duration_minutes") or (group.duration_minutes if group else None) or 60
    duration = int(duration)
    if not 10 <= duration <= 600:
        raise ValueError("La duracion debe estar entre 10 y 600 minutos.")

    tz = payload.get("timezone") or (group.timezone if group else None)
    if not tz:
        if group:
            # Caer en UTC aca daria un resultado corrido varias horas sin avisar.
            raise ValueError(
                "No se pudo determinar la zona horaria del grupo. Pega el enlace del "
                "grupo, o elegi la zona a mano en la busqueda manual."
            )
        tz = "UTC"
    buffer_minutes = int(payload.get("buffer_minutes") or 0)
    max_groups = payload.get("max_groups")
    max_groups = int(max_groups) if max_groups not in (None, "") else None

    ref_raw = payload.get("reference_date")
    reference_date = date.fromisoformat(ref_raw) if ref_raw else date.today()

    # Filtro de sucursal: por defecto la del grupo. Para GCC es lo correcto porque
    # el idioma del grupo depende de la sucursal.
    branch = payload.get("business_branch")
    if branch in (None, "") and payload.get("filter_by_branch", True) and group:
        branch = group.branch_id
    if isinstance(branch, str) and branch.strip().upper() in BRANCH_IDS:
        branch = BRANCH_IDS[branch.strip().upper()]
    branch = int(branch) if branch not in (None, "") else None

    requests = [
        GroupRequest(
            course=str(course_id),
            weekday=s.weekday,
            hour=s.hour,
            minute=s.minute,
            duration_minutes=duration,
            timezone=tz,
            reference_date=reference_date,
            buffer_minutes=buffer_minutes,
        )
        for s in sessions
    ]

    adapter = KodlandAdapter(client)
    started = datetime.now()

    stubs, grid_meta = adapter.shortlist_multi(
        course_id,
        [(r.start_utc, duration) for r in requests],
        business_branch=branch,
    )
    # Ids con el curso habilitado, ANTES de recortar por max_verify: si se tomara
    # la lista recortada, los que quedan afuera por el tope apareceran despues
    # etiquetados como "no tiene el curso", que es falso.
    with_course_ids = {int(s["id"]) for s in stubs if s.get("id") is not None}

    limit = int(payload.get("max_verify") or 60)
    truncated = max(0, len(stubs) - limit)
    if truncated:
        # Se verifican primero los menos cargados: son los que mas probablemente
        # terminen recomendados.
        stubs = sorted(stubs, key=lambda s: s.get("groups_count") or 0)[:limit]

    tutors, errors = adapter.build_tutors(
        stubs,
        reference_date=reference_date,
        include_extra_lessons=payload.get("include_extra_lessons", True),
        include_special_lessons=payload.get("include_special_lessons", True),
        max_groups=max_groups,
        course_id=course_id,
        group_kind_id=group.group_kind_id if group else None,
    )

    result = find_candidates(tutors, requests)

    # Enlace al perfil del tutor en el BackOffice, para poder verificar de un clic.
    for bucket in ("eligible", "rejected"):
        for candidate in result[bucket]:
            candidate["profile_url"] = teacher_url(candidate.get("tutor_id"))

    # Diagnostico OPCIONAL, apagado por defecto.
    #
    # La busqueda normal solo considera tutores que tienen el curso habilitado en
    # su propio catalogo: eso lo garantiza `availability_summary?course=`, y los
    # demas nunca llegan al motor. Este bloque hace una consulta EXTRA sin filtro
    # de curso, solo para poder responder "por que no me aparece fulano": los
    # muestra como descartados con el motivo. Al estar prendido por defecto daba la
    # impresion de que la busqueda miraba a todos los tutores, asi que ahora hay
    # que pedirlo explicitamente.
    if payload.get("show_course_excluded", False):
        try:
            extras = adapter.excluded_by_course(
                course_id,
                [(r.start_utc, duration) for r in requests],
                with_course_ids,
                business_branch=branch,
            )
        except KodlandApiError:
            extras = []
        for stub in extras:
            result["rejected"].append(
                {
                    "tutor_id": str(stub.get("id")),
                    "name": stub.get("full_name") or f"Tutor {stub.get('id')}",
                    "profile_url": teacher_url(stub.get("id")),
                    "timezone": None,
                    "utc_offset_minutes": None,
                    "eligible": False,
                    "blockers": ["no_dicta_el_curso"],
                    "blocker_labels": ["No tiene este curso asignado"],
                    "conflicts": [],
                    "current_load": stub.get("groups_count"),
                    "max_groups": max_groups,
                    "free_slots": None,
                    "courses": [],
                    "score": 0,
                    "notes": [
                        "Su horario si da, pero el curso no figura entre los que dicta. "
                        "Si deberia dictarlo, hay que asignarselo en el BackOffice."
                    ],
                    "extra": {"team_lead": stub.get("team_lead"), "unverified": True},
                }
            )
        result["summary"]["rejected"] = len(result["rejected"])
        result["summary"]["excluded_by_course"] = len(extras)

    result["group"] = group.as_dict() if group else None
    result["diagnostics"] = {
        "grid": grid_meta,
        "verified": len(tutors),
        "verify_errors": errors,
        "truncated_shortlist": truncated,
        "verify_limit": limit,
        "business_branch": branch,
        "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
        "reference_date": reference_date.isoformat(),
    }
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "KodlandTutorMatcher/1.0"

    def log_message(self, fmt: str, *args) -> None:  # menos ruido en consola
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- auth por pedido ------------------------------------------------------

    def _token_from_header(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token:
                return token
        return None

    def _require_client(self) -> KodlandClient:
        token = self._token_from_header()
        if not token:
            raise PermissionError("Falta el token. Pegalo en la interfaz para empezar.")
        return KodlandClient(token, BASE_URL)

    # -- respuestas ---------------------------------------------------------

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "No se encontro el archivo."}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, PermissionError):
            self._send_json({"error": str(exc), "needs_token": True}, 401)
        elif isinstance(exc, KodlandApiError):
            self._send_json(
                {"error": str(exc), "needs_token": exc.status == 401, "api_status": exc.status},
                502 if exc.status not in (401, 403) else exc.status,
            )
        elif isinstance(exc, (ValueError, KeyError)):
            self._send_json({"error": str(exc)}, 400)
        else:
            traceback.print_exc()
            self._send_json({"error": f"Error inesperado: {exc}"}, 500)

    # -- ruteo ----------------------------------------------------------------

    _KNOWN_ROUTES = (
        "/", "/index.html", "/api/status", "/api/courses",
        "/api/auth", "/api/group", "/api/search", "/api/logout",
    )

    def _route_and_query(self) -> tuple[str, dict]:
        """Determina la ruta logica del pedido.

        En local, `self.path` ya es la ruta real (p.ej. `/api/auth`). En
        Vercel, todas las rutas bajo `/api/*` llegan a `api/index.py` por un
        rewrite (ver `vercel.json`) que manda el sub-path real como query
        string (`?route=...`) en vez de en el path literal; esto lo recupera
        si hace falta, sin cambiar el comportamiento local.
        """
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        if route not in self._KNOWN_ROUTES:
            for key in ("route", "path"):
                if key in query:
                    sub = query[key][0].strip("/")
                    candidate = f"/api/{sub}" if sub else "/api"
                    if candidate in self._KNOWN_ROUTES:
                        route = candidate
                    break
        return route, query

    # -- verbos -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        route, query = self._route_and_query()
        try:
            if route in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            elif route == "/api/status":
                self._send_json({"ok": True})
            elif route == "/api/courses":
                self._send_json(handle_courses(self._require_client(), query))
            else:
                self._send_json({"error": "Ruta no encontrada."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        route, _query = self._route_and_query()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")

            if route == "/api/auth":
                self._send_json(handle_auth(payload))
            elif route == "/api/group":
                self._send_json(handle_group(self._require_client(), payload))
            elif route == "/api/search":
                self._send_json(handle_search(self._require_client(), payload))
            elif route == "/api/logout":
                # No hay nada guardado en el servidor: el navegador es quien
                # se olvida el token. Se deja la ruta por compatibilidad.
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "Ruta no encontrada."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Buscador de tutores por disponibilidad y curso.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    env_token = os.environ.get("KODLAND_TOKEN", "").strip()
    if env_token:
        try:
            user = KodlandClient(env_token, BASE_URL).me()
            print(f"KODLAND_TOKEN es valido (sesion de {user.get('email')}), pero igual hay que")
            print("pegarlo una vez en la interfaz: el servidor ya no guarda tokens entre pedidos.")
        except Exception as exc:  # noqa: BLE001
            print(f"El token de KODLAND_TOKEN no sirvio: {exc}", file=sys.stderr)

    url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Buscador de tutores en {url}")
    print(f"API: {BASE_URL}")
    print("Ctrl+C para detener.\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nListo, servidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

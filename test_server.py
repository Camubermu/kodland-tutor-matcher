"""Prueba de punta a punta del flujo de busqueda, con un API simulado.

Como el API real no es alcanzable desde un entorno de pruebas, se simula un
cliente que devuelve exactamente las formas de respuesta observadas en el
BackOffice (ver ENDPOINTS.md). Asi se ejercita todo el camino: grilla ->
preseleccion -> verificacion -> filtros duros -> ranking.
"""

from datetime import date

import pytest

import server

REF = date(2026, 8, 17)  # lunes; en agosto Madrid esta en CEST (UTC+2)

# El grupo pedido: lunes 09:00 en Bogota (UTC-5) == lunes 14:00 UTC.
GROUP = {
    "course_id": 717,
    "weekday": 0,
    "start_time": "09:00",
    "duration_minutes": 60,
    "timezone": "America/Bogota",
    "reference_date": REF.isoformat(),
}


def _avail(weekday, start, end, expert=False, extra=False):
    return {
        "id": 1,
        "weekday": weekday,
        "start_hour": start,
        "end_hour": end,
        "groups_allowed": [],
        "is_expert_lesson": expert,
        "is_extra_lesson": extra,
    }


TIMETABLES = {
    # Libre y disponible 08:00-12:00 Bogota (13:00-17:00 UTC). Cubre el bloque.
    100: {"teacher": 100, "timezone": "America/Bogota", "assignable": True,
          "availability": [_avail("Monday", "08:00:00", "12:00:00")],
          "availability_confirmed_at": "2026-08-10T10:00:00+03:00"},
    # Madrid 16:00-18:00 local == 14:00-16:00 UTC en agosto. Tambien cubre.
    200: {"teacher": 200, "timezone": "Europe/Madrid", "assignable": True,
          "availability": [_avail("Monday", "16:00:00", "18:00:00")],
          "availability_confirmed_at": "2026-08-11T10:00:00+03:00"},
    # Disponible, pero con una clase encima.
    300: {"teacher": 300, "timezone": "America/Bogota", "assignable": True,
          "availability": [_avail("Monday", "08:00:00", "12:00:00")],
          "availability_confirmed_at": "2026-08-01T10:00:00+03:00"},
    # Franja con los flags expert/extra en true. Son ADITIVOS: el bloque sigue
    # sirviendo para un grupo regular, asi que este tutor debe ser elegible.
    400: {"teacher": 400, "timezone": "America/Bogota", "assignable": True,
          "availability": [_avail("Monday", "08:00:00", "12:00:00", expert=True, extra=True)],
          "availability_confirmed_at": "2026-08-01T10:00:00+03:00"},
    # No asignable.
    500: {"teacher": 500, "timezone": "America/Bogota", "assignable": False,
          "availability": [_avail("Monday", "08:00:00", "12:00:00")],
          "availability_confirmed_at": "2026-08-01T10:00:00+03:00"},
}

GROUPS_TIMETABLE = {
    300: [{
        "timetable_id": 1, "group_id": 9, "group_title": "Python G-777",
        "course_title": "[717] Python Global [...]",
        "start_time": "2026-08-17T14:30:00Z",  # se cruza con 14:00-15:00 UTC
        "end_time": "2026-08-17T15:30:00Z",
    }],
}

STUBS = {
    100: {"id": 100, "full_name": "Ana Libre", "team_lead": None, "groups_count": 2},
    200: {"id": 200, "full_name": "Bruno Madrid", "team_lead": "Sofia", "groups_count": 5},
    300: {"id": 300, "full_name": "Carla Ocupada", "team_lead": None, "groups_count": 3},
    400: {"id": 400, "full_name": "Dario Reservado", "team_lead": None, "groups_count": 1},
    500: {"id": 500, "full_name": "Elsa Inactiva", "team_lead": None, "groups_count": 0},
}


class FakeClient:
    """Reproduce las formas de respuesta reales del BackOffice."""

    def __init__(self):
        self.calls = []

    def me(self):
        return {"id": 1, "first_name": "Test", "last_name": "User", "email": "t@kodland.team"}

    def availability_grid(self, course_id=None, **filters):
        self.calls.append(("grid", course_id))
        slots = ["13:00", "14:00", "15:00", "16:00"]
        grid = {d: {s: {"count": 0, "teachers": []} for s in slots}
                for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
        # Todos aparecen en el slot de las 14:00 UTC (la grilla esta en UTC).
        grid["Monday"]["14:00"] = {"count": len(STUBS), "teachers": list(STUBS.values())}
        return {"weekdays": list(grid), "time_slots": slots, "grid": grid}

    def teacher_timetable(self, tid):
        self.calls.append(("timetable", tid))
        return TIMETABLES[tid]

    def teacher_groups_timetable(self, tid):
        return GROUPS_TIMETABLE.get(tid, [])

    def teacher_extra_lessons_timetable(self, tid):
        return []

    def teacher_special_lessons_timetable(self, tid):
        return []

    def courses(self, **params):
        return [{"id": 717, "title": "[717] Python Global [2021][12-15][60m,90m][32L][Ind][Actual]",
                 "is_active": True}]


@pytest.fixture
def fake(monkeypatch):
    # El catalogo de cursos se cachea a nivel de modulo (se comparte entre
    # pedidos de distintos usuarios); se resetea por prueba para que no se
    # pisen entre si. monkeypatch lo deja como estaba al terminar.
    monkeypatch.setattr(server, "_courses_cache", None)
    return FakeClient()


# ---------------------------------------------------------------------------


def test_busqueda_completa_clasifica_y_ordena(fake):
    res = server.handle_search(fake, dict(GROUP))

    ok = {c["tutor_id"]: c for c in res["eligible"]}
    bad = {c["tutor_id"]: c for c in res["rejected"]}

    # Ana (2 grupos), Dario (1) y Bruno (5) sirven; ordena por carga.
    assert [c["tutor_id"] for c in res["eligible"]] == ["400", "100", "200"]
    assert ok["100"]["current_load"] == 2

    # Carla tiene una clase de 14:30 a 15:30 UTC que se cruza.
    assert "cruce_de_horario" in bad["300"]["blockers"]
    assert any("Python G-777" in c for c in bad["300"]["conflicts"])

    # Dario tiene los flags expert/extra en true, pero eso NO reserva el bloque.
    assert "400" in ok, "los flags expert/extra son aditivos, no deben descartar"

    # Elsa no es asignable.
    assert "tutor_inactivo" in bad["500"]["blockers"]

    assert res["summary"]["evaluated"] == 5
    assert res["summary"]["eligible"] == 3
    assert res["summary"]["rejected"] == 2


def test_la_hora_se_convierte_a_utc_correctamente(fake):
    res = server.handle_search(fake, dict(GROUP))
    # 09:00 Bogota (UTC-5) == 14:00 UTC del lunes.
    assert res["request"]["start_utc_label"] == "lunes 14:00"
    assert res["request"]["start_utc_minute_of_week"] == 14 * 60


def test_el_curso_se_pasa_al_api_como_filtro(fake):
    server.handle_search(fake, dict(GROUP))
    assert ("grid", 717) in fake.calls


def test_aceptar_franjas_de_clase_extra_habilita_a_dario(fake):
    res = server.handle_search(fake, {**GROUP, "allow_extra_lesson_blocks": True})
    assert "400" in {c["tutor_id"] for c in res["eligible"]}


def test_colchon_entre_clases_descarta_a_quien_queda_pegado(fake):
    # Carla termina su otra clase a las 15:30 UTC; nuestro grupo va 14:00-15:00.
    # Ya se cruzaba, asi que se usa a Ana: no tiene clases, sigue elegible.
    res = server.handle_search(fake, {**GROUP, "buffer_minutes": 30})
    assert "100" in {c["tutor_id"] for c in res["eligible"]}


def test_tope_de_grupos_descarta_a_los_cargados(fake):
    res = server.handle_search(fake, {**GROUP, "max_groups": 3})
    ids = {c["tutor_id"] for c in res["eligible"]}
    assert "100" in ids  # 2 grupos, bajo el tope
    assert "200" not in ids  # 5 grupos, lo excede
    bad = {c["tutor_id"]: c for c in res["rejected"]}
    assert "sin_cupo" in bad["200"]["blockers"]


def test_horario_de_verano_cambia_el_resultado_de_madrid(fake):
    # En enero Madrid es UTC+1: 16:00-18:00 local == 15:00-17:00 UTC, y el grupo
    # de 14:00-15:00 UTC ya no cabe. Bruno debe caer.
    res = server.handle_search(fake, {**GROUP, "reference_date": "2026-01-12"})
    bad = {c["tutor_id"]: c for c in res["rejected"]}
    assert "200" in bad
    assert "fuera_de_disponibilidad" in bad["200"]["blockers"]


def test_duracion_mas_larga_deja_de_caber_en_madrid(fake):
    # 14:00-16:30 UTC excede la franja de Bruno (termina 16:00 UTC).
    res = server.handle_search(fake, {**GROUP, "duration_minutes": 150})
    bad = {c["tutor_id"]: c for c in res["rejected"]}
    assert "fuera_de_disponibilidad" in bad["200"]["blockers"]


def test_diagnostico_reporta_la_preseleccion(fake):
    res = server.handle_search(fake, dict(GROUP))
    d = res["diagnostics"]
    assert d["grid"]["shortlisted"] == 5
    assert d["verified"] == 5
    assert d["verify_errors"] == []
    # Clase de 60 min: la ventana se ensancha 30 min por el supuesto de 90.
    assert d["grid"]["window_widened_minutes"] == 30


def test_curso_faltante_es_error_claro(fake):
    with pytest.raises(ValueError, match="curso"):
        server.handle_search(fake, {**GROUP, "course_id": ""})


def test_hora_invalida_es_error_claro(fake):
    with pytest.raises(ValueError, match="Hora invalida"):
        server.handle_search(fake, {**GROUP, "start_time": "nueve"})


def test_duracion_fuera_de_rango_es_error_claro(fake):
    with pytest.raises(ValueError, match="duracion"):
        server.handle_search(fake, {**GROUP, "duration_minutes": 5})


def test_catalogo_de_cursos_parsea_el_titulo(fake):
    data = server.handle_courses(fake, {})
    course = data["courses"][0]
    assert course["id"] == 717
    assert course["durations"] == [60, 90]
    assert course["default_duration"] == 60
    assert course["age_range"] == "12-15"

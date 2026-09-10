"""Busqueda a partir de un grupo real (GCC 72219), de punta a punta.

Verifica que pegar el enlace alcance: que el curso, las sesiones, la duracion, la
zona horaria y la sucursal salgan del API y lleguen bien hasta el resultado.
"""

import pytest

import server
from test_groups import GCC_COURSES, GROUP_72219, SCHEDULE_72219

# El grupo se reune sabado 16:00 en Asia/Riyadh (UTC+3) == sabado 13:00 UTC.
SAT = 5
SLOT_UTC = "13:00"

TEACHERS = {
    600: {"id": 600, "full_name": "Ismail Mohamed Hanein", "team_lead": None, "groups_count": 7},
    601: {"id": 601, "full_name": "Mostafa Enas", "team_lead": None, "groups_count": 10},
    602: {"id": 602, "full_name": "Licropani Flavia", "team_lead": None, "groups_count": 14},
}

# Todos en Riyadh, disponibles sabado 14:00-18:00 local (= 11:00-15:00 UTC).
TIMETABLES = {
    tid: {
        "teacher": tid,
        "timezone": "Asia/Riyadh",
        "assignable": True,
        "availability": [{
            "id": 1, "weekday": "Saturday", "start_hour": "14:00:00", "end_hour": "18:00:00",
            "groups_allowed": [], "is_expert_lesson": False, "is_extra_lesson": False,
        }],
        "availability_confirmed_at": "2026-08-12T10:00:00+03:00",
    }
    for tid in TEACHERS
}

# Mostafa ya tiene una clase que pisa el bloque (13:30-14:30 UTC).
GROUPS_TIMETABLE = {
    601: [{
        "timetable_id": 5, "group_id": 99, "group_title": "GCC ME Premium 40",
        "start_time": "2026-09-12T13:30:00Z", "end_time": "2026-09-12T14:30:00Z",
    }],
}


class FakeClient:
    def __init__(self):
        self.calls = []

    # -- grupo --
    def student_groups(self, **params):
        self.calls.append(("student_groups", params))
        # Los grupos "GCC ME ... (8-9 yo)" usan el curso 2014, igual que el 72219.
        return [
            {"id": 70000 + i, "title": f"GCC ME S-Premium {i} (SAT-16:00) (8-9 yo)",
             "course_id": 2014,
             "course_title": "[2014]Minecraft. Secret Level 1[2026][8-9][60 min][4 L][GCC][in progress]"}
            for i in range(5)
        ]

    def group_info(self, group_id):
        self.calls.append(("group_info", group_id))
        return GROUP_72219

    def group_schedule_view(self, group_id):
        return SCHEDULE_72219

    def courses(self, **params):
        self.calls.append(("courses", params))
        return GCC_COURSES

    # -- tutores --
    def availability_grid(self, course_id=None, **filters):
        self.calls.append(("grid", course_id, filters))
        slots = ["11:00", "12:00", SLOT_UTC, "14:00"]
        grid = {d: {s: {"count": 0, "teachers": []} for s in slots}
                for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]}
        grid["Saturday"][SLOT_UTC] = {"count": len(TEACHERS), "teachers": list(TEACHERS.values())}
        return {"weekdays": list(grid), "time_slots": slots, "grid": grid}

    def teacher_timetable(self, tid):
        return TIMETABLES[tid]

    def teacher_groups_timetable(self, tid):
        return GROUPS_TIMETABLE.get(tid, [])

    def teacher_extra_lessons_timetable(self, tid):
        return []

    def teacher_special_lessons_timetable(self, tid):
        return []

    def me(self):
        return {"id": 1, "first_name": "T", "last_name": "U", "email": "t@kodland.team"}


@pytest.fixture
def fake(monkeypatch):
    # Ver la nota en test_server.py: el catalogo de cursos se cachea a nivel
    # de modulo y hay que resetearlo para que las pruebas no se pisen.
    monkeypatch.setattr(server, "_courses_cache", None)
    return FakeClient()


LINK = "https://bo.kodland.org/groups/72219"
TITLE = "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)"


# ---------------------------------------------------------------------------


def test_resolver_el_grupo_por_enlace(fake):
    res = server.handle_group(fake, {"link": LINK})
    g = res["group"]
    assert g["group_id"] == 72219
    assert g["course_id"] == 2014
    assert g["duration_minutes"] == 60
    assert g["timezone"] == "Asia/Riyadh"
    assert g["branch_title"] == "GCC"
    assert g["unassigned"] is True
    assert g["ready"] is True
    assert g["sessions"][0]["weekday_name"] == "sabado"
    assert g["sessions"][0]["start_local"] == "16:00"


def test_buscar_con_solo_el_enlace(fake):
    res = server.handle_search(fake, {"link": LINK})
    # sabado 16:00 Riyadh == sabado 13:00 UTC
    assert res["request"]["start_utc_label"] == "sabado 13:00"
    assert res["request"]["duration_minutes"] == 60
    assert res["request"]["timezone"] == "Asia/Riyadh"

    ids = [c["tutor_id"] for c in res["eligible"]]
    # Ismail (7 grupos) antes que Licropani (14). Mostafa se cruza y cae.
    assert ids == ["600", "602"]
    bad = {c["tutor_id"]: c for c in res["rejected"]}
    assert "cruce_de_horario" in bad["601"]["blockers"]


def test_el_filtro_de_sucursal_se_aplica_por_defecto(fake):
    server.handle_search(fake, {"link": LINK})
    grid_calls = [c for c in fake.calls if c[0] == "grid"]
    assert grid_calls, "se esperaba una consulta a la grilla"
    assert grid_calls[0][1] == 2014, "debe filtrar por el curso del grupo"
    assert grid_calls[0][2].get("business_branch") == 12, "debe filtrar por GCC"


def test_se_puede_desactivar_el_filtro_de_sucursal(fake):
    server.handle_search(fake, {"link": LINK, "filter_by_branch": False})
    grid_calls = [c for c in fake.calls if c[0] == "grid"]
    assert grid_calls[0][2].get("business_branch") is None


def test_cada_candidato_trae_el_enlace_a_su_perfil(fake):
    res = server.handle_search(fake, {"link": LINK})
    for bucket in ("eligible", "rejected"):
        for c in res[bucket]:
            assert c["profile_url"] == f"https://bo.kodland.org/teachers/{c['tutor_id']}", c


def test_solo_el_enlace_alcanza_sin_pasar_el_nombre(fake):
    # El campo del nombre ya no existe en la interfaz: el enlace debe bastar.
    res = server.handle_search(fake, {"link": LINK})
    assert res["group"]["course_id"] == 2014
    assert res["group"]["duration_minutes"] == 60
    assert res["request"]["start_utc_label"] == "sabado 13:00"
    assert res["eligible"]


def test_el_id_suelto_tambien_alcanza(fake):
    res = server.handle_search(fake, {"link": "72219"})
    assert res["group"]["group_id"] == 72219


def test_el_nombre_pegado_en_el_campo_del_enlace_sigue_funcionando(fake):
    # Un solo campo: si no hay id, se interpreta como nombre.
    res = server.handle_search(fake, {"link": TITLE})
    assert res["group"]["source"] == "title"
    assert res["group"]["course_id"] == 2014


def test_el_markdown_completo_en_el_campo_del_enlace(fake):
    res = server.handle_search(fake, {"link": f"[{TITLE}]({LINK})"})
    assert res["group"]["source"] == "api"
    assert res["group"]["group_id"] == 72219


def test_el_grupo_viaja_en_la_respuesta(fake):
    res = server.handle_search(fake, {"link": LINK})
    assert res["group"]["group_id"] == 72219
    assert res["group"]["course_name"] == "Minecraft. Secret Level 1"


def test_solo_el_nombre_ya_alcanza_para_gcc(fake):
    # Con el mapa de codigos, pegar solo el nombre resuelve curso y duracion.
    res = server.handle_search(fake, {"title": TITLE})
    assert res.get("needs_input") is not True
    # Resuelto desde los grupos reales, no desde una tabla fija.
    assert res["group"]["course_id"] == 2014
    assert res["request"]["duration_minutes"] == 60
    assert res["request"]["start_utc_label"] == "sabado 13:00"


def test_un_codigo_desconocido_si_pide_elegir(fake):
    # "ZZ" no esta en el mapa ni coincide con nada del catalogo.
    res = server.handle_search(fake, {"title": "GCC ZZ S-Premium 1 (SAT-16:00) (8-9 yo)"})
    assert res.get("needs_input") is True
    assert res["group"]["course_id"] is None


def test_solo_el_nombre_con_curso_elegido_a_mano(fake):
    res = server.handle_search(fake, {"title": TITLE, "course_id": 2014})
    assert res["request"]["course"] == "2014"
    assert res["request"]["start_utc_label"] == "sabado 13:00"


def test_el_nombre_con_markdown_y_enlace_incluido_usa_el_api(fake):
    res = server.handle_search(fake, {"title": f"[{TITLE}]({LINK})"})
    assert res["group"]["source"] == "api"
    assert res["request"]["duration_minutes"] == 60


def test_duracion_manual_pisa_la_del_grupo(fake):
    # Una clase de 180 min no cabe en 14:00-18:00 local si arranca 16:00... si cabe.
    # Con 300 min se pasa del final de la franja y nadie deberia quedar.
    res = server.handle_search(fake, {"link": LINK, "duration_minutes": 300})
    assert res["eligible"] == []
    assert all("fuera_de_disponibilidad" in c["blockers"] for c in res["rejected"])


def test_solo_el_nombre_usa_la_zona_de_gcc_no_utc(fake):
    # Regresion: sin esto la hora del titulo se tomaba como UTC y el resultado
    # salia corrido 3 horas, con candidatos que parecian correctos.
    res = server.handle_search(fake, {"title": TITLE, "course_id": 2014})
    assert res["request"]["timezone"] == "Asia/Riyadh"
    assert res["request"]["start_utc_label"] == "sabado 13:00"


def test_sin_zona_horaria_determinable_falla_claro(fake):
    with pytest.raises(ValueError, match="zona horaria"):
        server.handle_search(fake, {"title": "LATAM PY Premium 3 (MON-10:00) (10-12 yo)", "course_id": 2014})


def test_grupo_con_dos_sesiones_exige_estar_libre_en_ambas(fake, monkeypatch):
    dos = dict(GROUP_72219)
    dos["group_schedule"] = [
        {"day": 6, "time": "16:00"},   # sabado
        {"day": 1, "time": "16:00"},   # lunes (nadie esta disponible ese dia)
    ]
    monkeypatch.setattr(fake, "group_info", lambda gid: dos)
    res = server.handle_search(fake, {"link": LINK})
    assert len(res["request"]["sessions"]) == 2
    # La grilla no tiene a nadie el lunes, asi que la interseccion queda vacia.
    assert res["eligible"] == []
    assert res["diagnostics"]["grid"]["shortlisted"] == 0

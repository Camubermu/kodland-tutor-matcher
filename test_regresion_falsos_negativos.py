"""Regresiones de los falsos negativos reportados en pruebas manuales.

La causa era una sola: se interpretaron ``is_expert_lesson`` e ``is_extra_lesson``
como si **reservaran** el bloque para clases expertas o extra, y se descartaban.
En realidad son **aditivos** — "en esta franja tambien acepta clase experta/extra"
— y el bloque sigue sirviendo para un grupo regular.

La prueba que lo demuestra sobre datos reales: la tutora 3460750 declara
``Friday 13:00-17:00`` con ambos flags en ``true``, y ``availability_summary`` la
lista igual en esa franja (verificado en el slot Friday 12:00 UTC). Si los flags
reservaran el bloque, el propio API no la mostraria disponible.

El impacto era grande porque hay tutores con **todos** sus bloques marcados asi
(la 3232394 es un caso): quedaban con disponibilidad vacia y se descartaban por
"fuera de disponibilidad" teniendo la agenda libre.

Todos los datos de este archivo son reales, tomados del BackOffice.
"""

import pytest

import server

# --- Tutoras reales -------------------------------------------------------

AYA = 3460750       # Mohamed Noaman Aya — Asia/Riyadh, 0 grupos
ISMAIL = 3232394    # Ismail Mohamed Hanein — Africa/Cairo, 7 grupos
ALAA = 3265178      # Mahmoud Ali Alaa Ashraf — 1 grupo

GCC_KINDS = [3, 8, 13, 26, 30]


def _av(weekday, start, end, expert=True, extra=True):
    return {
        "id": 1, "weekday": weekday, "start_hour": start, "end_hour": end,
        "groups_allowed": GCC_KINDS,
        "is_expert_lesson": expert, "is_extra_lesson": extra,
    }


TIMETABLES = {
    # Bloques reales de Aya. Notar los flags en true en viernes y sabado.
    AYA: {
        "teacher": AYA, "timezone": "Asia/Riyadh", "assignable": True,
        "groups_allowed": GCC_KINDS,
        "availability": [
            _av("Monday", "23:00:00", "01:00:00", expert=False, extra=False),
            _av("Wednesday", "20:00:00", "00:00:00", expert=False, extra=False),
            _av("Thursday", "20:00:00", "23:00:00"),
            _av("Friday", "13:00:00", "17:00:00"),
            _av("Friday", "18:00:00", "21:00:00"),
            _av("Saturday", "13:00:00", "20:00:00"),
            _av("Sunday", "23:00:00", "01:00:00"),
        ],
        "availability_confirmed_at": "2026-08-12T10:00:00+03:00",
    },
    # Bloques reales de Ismail: TODOS con los flags en true.
    ISMAIL: {
        "teacher": ISMAIL, "timezone": "Africa/Cairo", "assignable": True,
        "groups_allowed": GCC_KINDS,
        "availability": [
            _av("Sunday", "00:00:00", "03:00:00"),
            _av("Sunday", "10:00:00", "23:59:00"),
            _av("Monday", "10:00:00", "23:59:00"),
            _av("Monday", "00:00:00", "02:55:00"),
            _av("Tuesday", "00:00:00", "08:00:00"),
            _av("Tuesday", "10:00:00", "23:55:00"),
            _av("Wednesday", "00:00:00", "02:00:00"),
            _av("Wednesday", "10:00:00", "23:55:00"),
            _av("Thursday", "00:00:00", "02:00:00"),
            _av("Thursday", "10:00:00", "23:55:00"),
            _av("Saturday", "00:00:00", "02:00:00"),
            _av("Saturday", "10:00:00", "23:55:00"),
        ],
        "availability_confirmed_at": "2026-08-12T10:00:00+03:00",
    },
    ALAA: {
        "teacher": ALAA, "timezone": "Africa/Cairo", "assignable": True,
        "groups_allowed": GCC_KINDS,
        "availability": [_av("Wednesday", "10:00:00", "23:00:00")],
        "availability_confirmed_at": "2026-08-12T10:00:00+03:00",
    },
}

STUBS = {
    AYA: {"id": AYA, "full_name": "Mohamed Noaman Aya", "team_lead": "Munoz Bermudez Camilo", "groups_count": 0},
    ISMAIL: {"id": ISMAIL, "full_name": "Ismail Mohamed Hanein", "team_lead": "Munoz Bermudez Camilo", "groups_count": 7},
    ALAA: {"id": ALAA, "full_name": "Mahmoud Ali Alaa Ashraf", "team_lead": "Munoz Bermudez Camilo", "groups_count": 1},
}

# Clase real de Ismail que produjo el cruce correcto del caso 3:
# "GCC ME L-Premium 24 (SAT-17:00)" == sabado 14:00 UTC, 90 min.
ISMAIL_LESSONS = [{
    "timetable_id": 1, "group_id": 1, "group_title": "GCC ME L-Premium 24 (SAT-17:00) (10-12 yo)",
    "start_time": "2026-08-22T14:00:00Z", "end_time": "2026-08-22T15:30:00Z",
}]


# Cursos realmente asignados en el BackOffice. Aya solo tiene Digital Creativity:
# por eso NO debe aparecer para un grupo de Minecraft Education, y ese descarte es
# correcto (no un falso negativo).
CURSOS_POR_TUTOR = {
    AYA: {2038, 2039},
    ISMAIL: {1844, 1847, 1848, 2038, 2039},
    ALAA: {1844, 2039},
}


# Que curso usa cada combinacion (codigo, edad) en este escenario. El resolutor
# lo lee de los grupos reales, asi que hay que darselos.
USO = {("DC", "10-12"): 2039, ("DC", "8-9"): 2038,
       ("PY", "13-17"): 1844, ("ME", "8-9"): 1847}
TITULOS = {
    2039: "[2039]Digital Creativity 2[2026][10-12][60 min][40 L][GCC][actual]",
    2038: "[2038]Digital Creativity. Level 1[2026][8-9][60 min][40 L][GCC][actual]",
    1844: "[1844]Python LVL1[2026][12-17][90 min][40 L][GCC][in progress]",
    1847: "[1847]Minecraft Education: Create Your World with Code![2026][8-9][60 min][40 L][GCC][in progress]",
}


class FakeClient:
    """Devuelve los datos reales, y una grilla que respeta el filtro de curso."""

    def __init__(self, lessons=None):
        self.lessons = lessons if lessons is not None else {ISMAIL: ISMAIL_LESSONS}

    def student_groups(self, **params):
        rows = []
        for i, ((code, edad), cid) in enumerate(USO.items()):
            low, high = edad.split("-")
            rows.append({
                "id": 80000 + i,
                "title": f"GCC {code} L-Premium {i} (SAT-16:00) ({low}-{high} yo)",
                "course_id": cid, "course_title": TITULOS[cid],
            })
        return rows

    def courses(self, **params):
        return [{"id": cid, "title": t, "is_active": True} for cid, t in TITULOS.items()]

    def availability_grid(self, course_id=None, **filters):
        # Grilla amplia en el tiempo: lo que se prueba aca no es la preseleccion
        # horaria, pero si el filtro de curso (para el caso 4).
        slots = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
        if course_id is None:
            stubs = list(STUBS.values())
        else:
            stubs = [s for tid, s in STUBS.items() if int(course_id) in CURSOS_POR_TUTOR[tid]]
        grid = {}
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            grid[day] = {s: {"count": len(stubs), "teachers": list(stubs)} for s in slots}
        return {"weekdays": list(grid), "time_slots": slots, "grid": grid}

    def teacher_timetable(self, tid):
        return TIMETABLES[tid]

    def teacher_groups_timetable(self, tid):
        return self.lessons.get(tid, [])

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


# Los grupos se buscan por nombre: el mapa de codigos ya resuelve curso y duracion.
def _buscar(client, titulo, **extra):
    payload = {"title": titulo, "reference_date": "2026-08-17", **extra}
    return server.handle_search(client, payload)


def _ids(res, key="eligible"):
    return [c["tutor_id"] for c in res[key]]


# ---------------------------------------------------------------------------
# Caso 1: GCC DC L-Standard 71 (FRI-15:00) (10-12 yo)  ->  Aya debe salir
# ---------------------------------------------------------------------------


def test_caso1_aya_es_elegible_el_viernes(fake):
    # 15:00 Riyadh = 12:00 UTC, 60 min. Aya declara viernes 13:00-17:00 local
    # (= 10:00-14:00 UTC) con expert/extra en true.
    res = _buscar(fake, "GCC DC L-Standard 71 (FRI-15:00) (10-12 yo)")
    assert res["request"]["start_utc_label"] == "viernes 12:00"
    assert res["request"]["duration_minutes"] == 60
    assert str(AYA) in _ids(res), f"Aya deberia poder tomarlo. Descartados: {res['rejected']}"


def test_caso1_aya_va_primera_por_no_tener_grupos(fake):
    res = _buscar(fake, "GCC DC L-Standard 71 (FRI-15:00) (10-12 yo)")
    assert _ids(res)[0] == str(AYA), "con 0 grupos deberia encabezar el ranking"


# ---------------------------------------------------------------------------
# Caso 2: GCC PY L-Standard 61 (WED-18:00) (13-17 yo)  ->  Ismail y Alaa
# ---------------------------------------------------------------------------


def test_caso2_ismail_y_alaa_son_elegibles_el_miercoles(fake):
    # 18:00 Riyadh = 15:00 UTC, 90 min -> 15:00-16:30 UTC.
    # Ismail declara miercoles 10:00-23:55 en Africa/Cairo (UTC+3 en agosto)
    # = 07:00-20:55 UTC. Cabe de sobra.
    res = _buscar(fake, "GCC PY L-Standard 61 (WED-18:00) (13-17 yo)")
    assert res["request"]["start_utc_label"] == "miercoles 15:00"
    assert res["request"]["duration_minutes"] == 90
    elegibles = _ids(res)
    assert str(ISMAIL) in elegibles, f"Descartados: {res['rejected']}"
    assert str(ALAA) in elegibles


def test_caso2_ismail_y_alaa_no_quedan_fuera_de_disponibilidad(fake):
    res = _buscar(fake, "GCC PY L-Standard 61 (WED-18:00) (13-17 yo)")
    fuera = {c["tutor_id"] for c in res["rejected"]
             if "fuera_de_disponibilidad" in c["blockers"]}
    assert str(ISMAIL) not in fuera
    assert str(ALAA) not in fuera


def test_aya_si_queda_fuera_cuando_el_horario_realmente_no_da(fake):
    # Mismo miercoles 18:00 Riyadh (= 15:00-16:00 UTC) pero con un curso que Aya
    # SI dicta. Su miercoles es 20:00-00:00 local (= 17:00-21:00 UTC), asi que no
    # alcanza: este descarte es correcto, no un falso negativo.
    res = _buscar(fake, "GCC DC L-Standard 45 (WED-18:00) (10-12 yo)")
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert str(AYA) in rechazados
    assert "fuera_de_disponibilidad" in rechazados[str(AYA)]["blockers"]


# ---------------------------------------------------------------------------
# Caso 4: GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)  ->  Aya debe salir
# ---------------------------------------------------------------------------


def test_caso4_aya_no_dicta_minecraft_education_y_ese_descarte_es_correcto(fake):
    # 16:00 Riyadh = 13:00 UTC, 60 min. El horario de Aya SI da (sabado 13:00-20:00
    # local = 10:00-17:00 UTC), pero en el BackOffice solo tiene asignados los
    # cursos 2038 y 2039 (Digital Creativity), no el 1847. No es un falso negativo.
    res = _buscar(fake, "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert res["request"]["start_utc_label"] == "sabado 13:00"
    assert str(AYA) not in _ids(res)

    # Por defecto ni siquiera se la menciona: la busqueda considera solo a quienes
    # tienen el curso habilitado.
    assert str(AYA) not in {c["tutor_id"] for c in res["rejected"]}


def test_el_diagnostico_opcional_explica_por_que_aya_no_aparece(fake):
    # Con el diagnostico prendido si se la lista, con el motivo explicito, para
    # poder distinguir "no puede a esa hora" de "no tiene el curso asignado".
    res = _buscar(fake, "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)", show_course_excluded=True)
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert str(AYA) in rechazados
    assert "no_dicta_el_curso" in rechazados[str(AYA)]["blockers"]
    assert rechazados[str(AYA)]["extra"]["unverified"] is True
    assert res["summary"]["excluded_by_course"] >= 1


def test_solo_se_evaluan_tutores_con_el_curso_habilitado(fake):
    # Ningun tutor sin el curso debe llegar al motor: los evaluados salen todos
    # del pool filtrado por curso.
    res = _buscar(fake, "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    evaluados = {c["tutor_id"] for c in res["eligible"]} | {c["tutor_id"] for c in res["rejected"]}
    con_curso = {str(t) for t, cs in CURSOS_POR_TUTOR.items() if 1847 in cs}
    assert evaluados <= con_curso, f"se evaluo a alguien sin el curso: {evaluados - con_curso}"


def test_el_tope_de_verificacion_no_marca_a_nadie_como_sin_curso(fake):
    # Con max_verify=1 se recorta la lista a verificar. Los recortados SI tienen el
    # curso: no deben aparecer etiquetados como "no lo dicta".
    res = _buscar(fake, "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)",
                  show_course_excluded=True, max_verify=1)
    mal = [c["tutor_id"] for c in res["rejected"]
           if "no_dicta_el_curso" in c["blockers"]
           and 1847 in CURSOS_POR_TUTOR.get(int(c["tutor_id"]), set())]
    assert mal == [], f"etiquetados como sin curso teniendolo: {mal}"


def test_caso4_si_da_el_horario_para_su_propio_curso(fake):
    # El mismo sabado 16:00, pero con un curso que Aya si dicta (DC 8-9 = 2038).
    res = _buscar(fake, "GCC DC S-Premium 48 (SAT-16:00) (8-9 yo)")
    assert str(AYA) in _ids(res), f"Descartados: {res['rejected']}"


# ---------------------------------------------------------------------------
# Caso 3: el que ya funcionaba. No debe romperse al arreglar los otros.
# ---------------------------------------------------------------------------


def test_caso3_el_cruce_real_sigue_detectandose(fake):
    # GCC PY L-Premium 73 (SAT-16:00) = 13:00 UTC, 90 min -> 13:00-14:30 UTC.
    # Ismail ya tiene "GCC ME L-Premium 24" de 14:00 a 15:30 UTC: se cruza.
    res = _buscar(fake, "GCC PY L-Premium 73 (SAT-16:00) (13-17 yo)")
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert str(ISMAIL) in rechazados, "el cruce del caso 3 debe seguir detectandose"
    assert "cruce_de_horario" in rechazados[str(ISMAIL)]["blockers"]
    assert any("L-Premium 24" in c for c in rechazados[str(ISMAIL)]["conflicts"])


def test_caso3_alaa_queda_fuera_por_disponibilidad_no_por_los_flags(fake):
    # Alaa solo declara miercoles, asi que un grupo de sabado no le sirve.
    res = _buscar(fake, "GCC PY L-Premium 73 (SAT-16:00) (13-17 yo)")
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert "fuera_de_disponibilidad" in rechazados[str(ALAA)]["blockers"]


# ---------------------------------------------------------------------------
# Que el arreglo no vuelva demasiado permisivo el filtro
# ---------------------------------------------------------------------------


def test_una_hora_realmente_fuera_de_disponibilidad_sigue_descartando(fake):
    # Aya no declara nada el viernes entre las 17:00 y las 18:00 local
    # (= 14:00-15:00 UTC): tiene 13-17 y 18-21. Un grupo de 60 min a las 17:00
    # local cae justo en el hueco.
    res = _buscar(fake, "GCC DC L-Standard 99 (FRI-17:00) (10-12 yo)")
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert str(AYA) in rechazados
    assert "fuera_de_disponibilidad" in rechazados[str(AYA)]["blockers"]


def test_un_bloque_que_no_alcanza_para_la_duracion_sigue_descartando(fake):
    # Aya el jueves declara 20:00-23:00 local. Un grupo DC de 60 min a las 22:30
    # local terminaria 23:30: se pasa del final de la franja.
    res = _buscar(fake, "GCC DC L-Standard 98 (THU-22:30) (10-12 yo)")
    rechazados = {c["tutor_id"]: c for c in res["rejected"]}
    assert str(AYA) in rechazados
    assert "fuera_de_disponibilidad" in rechazados[str(AYA)]["blockers"]


def test_los_flags_quedan_como_informacion_no_como_filtro(fake):
    res = _buscar(fake, "GCC DC S-Premium 48 (SAT-16:00) (8-9 yo)")
    aya = next(c for c in res["eligible"] if c["tutor_id"] == str(AYA))
    # Se informan, pero no descartaron.
    assert aya["extra"]["blocks_also_expert"] > 0
    assert aya["extra"]["blocks_also_extra"] > 0


def test_cairo_en_enero_corre_una_hora(fake):
    # Africa/Cairo es UTC+2 en enero y UTC+3 en agosto. El miercoles de Ismail
    # (10:00-23:55 local) en enero es 08:00-21:55 UTC, y el grupo de 15:00 UTC
    # sigue cabiendo; la prueba fija que el offset se resuelve por fecha.
    ago = _buscar(fake, "GCC PY L-Standard 61 (WED-18:00) (13-17 yo)", reference_date="2026-08-17")
    ene = _buscar(fake, "GCC PY L-Standard 61 (WED-18:00) (13-17 yo)", reference_date="2026-01-12")
    off_ago = next(c for c in ago["eligible"] if c["tutor_id"] == str(ISMAIL))["utc_offset_minutes"]
    off_ene = next(c for c in ene["eligible"] + ene["rejected"] if c["tutor_id"] == str(ISMAIL))["utc_offset_minutes"]
    assert off_ago == 180
    assert off_ene == 120

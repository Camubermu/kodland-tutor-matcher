"""Pruebas de la resolucion de grupos, con el payload real del grupo 72219.

El punto mas delicado que se prueba aca es la **convencion de dias**: el API usa
0=domingo..6=sabado y este proyecto usa 0=lunes..6=domingo. Un error ahi desplaza
todo un dia sin lanzar ninguna excepcion, y el resultado se ve perfectamente
razonable.
"""

import pytest

from groups import (
    API_DAY_TO_PYTHON,
    BRANCH_IDS,
    UNASSIGNED_TEACHER_ID,
    GroupResolver,
    extract_group_id,
    extract_title,
    parse_group_title,
)

# Respuesta real de /student_groups/72219/get_general_info_for_group_backoffice_page/
GROUP_72219 = {
    "group_id": 72219,
    "group_title": "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)",
    "group_is_premium": True,
    "group_is_archive": False,
    "group_teacher": {"id": 245461, "full_name": "------ default teacher"},
    "group_curator": {"id": 2456235, "full_name": "Munoz Bermudez Camilo"},
    "group_kind": {"id": 13, "title": "[13] Mini grupo | Media, 4 estudiantes, 60 minutos"},
    "group_language": {"id": 10, "title": "Arabic"},
    "group_timezone": {"name": "Asia/Riyadh", "name_with_offset": "Asia/Riyadh (UTC+03:00)",
                       "offset_utc": "UTC+03:00"},
    "group_start_time": "2026-09-12 16:00:00",
    "course": {"id": 2014,
               "title": "[2014]Minecraft. Secret Level 1[2026][8-9][60 min][4 L][GCC][in progress]"},
    "max_students": 4,
    "min_age_of_students": 8,
    "max_age_of_students": 9,
    "lesson_length": 60,
    "business_branch": {"id": 12, "title": "GCC", "allowed_group_types": [3, 8, 13, 26, 30]},
    "all_lessons_count": 9,
    "group_schedule": [{"day": 6, "time": "16:00", "is_first_lesson": True,
                        "first_lesson_date": "2026-09-12"}],
    "predicted_end_date": "2026-11-07 16:00:00",
}

# Respuesta real de /student_groups/72219/schedule_view/ (recortada).
# 16:00 en Asia/Riyadh (UTC+3) == 13:00 UTC. Este cruce es el que valida la zona.
SCHEDULE_72219 = [
    {"timetable_id": 2980549, "timetable_time": "2026-09-12T13:00:00Z",
     "lesson_number": 1, "course_id": 2014, "teacher_id": 245461},
    {"timetable_id": 2980550, "timetable_time": "2026-09-19T13:00:00Z",
     "lesson_number": 2, "course_id": 2014, "teacher_id": 245461},
]

# Titulos reales de los cursos de GCC (business_branch=12).
# Los 1844-1848 son los que usan los grupos reales; los 2006/2014/1862 son los que
# hacen ambigua la busqueda por texto de "ME" y por eso existe el mapa de codigos.
GCC_COURSES = [
    {"id": 2039, "title": "[2039]Digital Creativity 2[2026][10-12][60 min][40 L][GCC][actual]", "is_active": True},
    {"id": 2038, "title": "[2038]Digital Creativity. Level 1[2026][8-9][60 min][40 L][GCC][actual]", "is_active": True},
    {"id": 2014, "title": "[2014]Minecraft. Secret Level 1[2026][8-9][60 min][4 L][GCC][in progress]", "is_active": True},
    {"id": 2006, "title": "[2006]Minecraft. Secret Level 2[2026][10-11][90 min][4 L][GCC][in progress]", "is_active": True},
    {"id": 1862, "title": "[1862]Minecraft in Scratch[2026][8-9][40 min][1 L][GCC][actual]", "is_active": True},
    {"id": 1861, "title": "[1861]Masterclass. Roblox game creation copy[2026][8-12][58 min][1 L][GCC][actual]", "is_active": True},
    {"id": 1848, "title": "[1848]Minecraft Education LVL2[2026][10-11][90 min][40 L][GCC][in progress]", "is_active": True},
    {"id": 1847, "title": "[1847]Minecraft Education: Create Your World with Code![2026][8-9][60 min][40 L][GCC][in progress]", "is_active": True},
    {"id": 1846, "title": "[1846]Roblox Game Developer[2026][10-12][90 min][40 L][GCC][in progress]", "is_active": True},
    {"id": 1845, "title": "[1845]Roblox Game Developer[2026][8-9][60 min][40 L][GCC][in progress]", "is_active": True},
    {"id": 1844, "title": "[1844]Python LVL1[2026][12-17][90 min][40 L][GCC][in progress]", "is_active": True},
]


# Distribucion real de cursos por (codigo, edad) entre los 135 grupos de GCC,
# medida contra el sistema. Varias claves usan MAS DE UN curso, y para "ME" el
# mayoritario no es el que sugeria la muestra inicial de 73 grupos.
USO_REAL_GCC = {
    ("ME", "8-9"): {2014: 9, 1847: 3, 2007: 1},
    ("ME", "10-12"): {2006: 10, 1848: 5},
    ("RO", "8-9"): {1845: 13, 1718: 1},
    ("RO", "10-12"): {1846: 16},
    ("PY", "13-17"): {1844: 14},
    ("DC", "8-9"): {2038: 11},
    ("DC", "10-12"): {2039: 21, 1603: 2},
}

TITULOS_CURSO = {c["id"]: c["title"] for c in GCC_COURSES}
TITULOS_CURSO.update({
    2006: "[2006]Minecraft. Secret Level 2[2026][10-11][90 min][4 L][GCC][in progress]",
    2007: "[2007]Minecraft. Secret Level 1b[2026][8-9][60 min][4 L][GCC][in progress]",
    1718: "[1718]Roblox viejo[2024][8-9][60 min][40 L][GCC][in progress]",
    1603: "[1603]Digital Creativity viejo[2024][10-12][60 min][40 L][GCC][in progress]",
})


def _gcc_group_rows() -> list[dict]:
    """Filas como las devuelve GET /student_groups/?business_branch=12."""
    rows, n = [], 0
    for (code, edad), cursos in USO_REAL_GCC.items():
        low, high = edad.split("-")
        for course_id, veces in cursos.items():
            for _ in range(veces):
                n += 1
                rows.append({
                    "id": 70000 + n,
                    "title": f"GCC {code} L-Premium {n} (SAT-16:00) ({low}-{high} yo)",
                    "course_id": course_id,
                    "course_title": TITULOS_CURSO.get(course_id, f"[{course_id}]Curso"),
                })
    return rows


GCC_GROUP_ROWS = _gcc_group_rows()


class FakeClient:
    def __init__(self, group=None, schedule=None, courses=None, group_rows=None):
        self.group = group if group is not None else GROUP_72219
        self.schedule = schedule if schedule is not None else SCHEDULE_72219
        self._courses = courses if courses is not None else GCC_COURSES
        self._group_rows = GCC_GROUP_ROWS if group_rows is None else group_rows
        self.calls = []

    def student_groups(self, **params):
        self.calls.append(("student_groups", params))
        if self._group_rows is False:
            raise RuntimeError("el API de grupos no respondio")
        return list(self._group_rows)

    def group_info(self, group_id):
        self.calls.append(("group_info", group_id))
        return self.group

    def group_schedule_view(self, group_id):
        self.calls.append(("schedule_view", group_id))
        return self.schedule

    def courses(self, **params):
        self.calls.append(("courses", params))
        return self._courses


@pytest.fixture
def resolver():
    return GroupResolver(FakeClient())


# ---------------------------------------------------------------------------
# Convencion de dias
# ---------------------------------------------------------------------------


def test_convencion_de_dias_del_api():
    # API: 0=domingo..6=sabado. Proyecto: 0=lunes..6=domingo.
    assert API_DAY_TO_PYTHON[6] == 5, "day=6 del API es sabado, que en Python es 5"
    assert API_DAY_TO_PYTHON[0] == 6, "day=0 del API es domingo, que en Python es 6"
    assert API_DAY_TO_PYTHON[1] == 0, "day=1 del API es lunes, que en Python es 0"
    assert sorted(API_DAY_TO_PYTHON.values()) == list(range(7))


def test_el_dia_del_grupo_real_es_sabado(resolver):
    spec = resolver.from_api(72219)
    assert len(spec.sessions) == 1
    session = spec.sessions[0]
    assert session.weekday == 5
    assert session.label == "sabado 16:00"


# ---------------------------------------------------------------------------
# Extraccion de id y titulo
# ---------------------------------------------------------------------------


def test_id_desde_enlace():
    assert extract_group_id("https://bo.kodland.org/groups/72219") == 72219


def test_id_desde_markdown():
    md = "[GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)](https://bo.kodland.org/groups/72219)"
    assert extract_group_id(md) == 72219
    assert extract_title(md) == "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)"


def test_id_desde_numero_suelto():
    assert extract_group_id("72219") == 72219
    assert extract_group_id(" #72219 ") == 72219


def test_sin_id_devuelve_none():
    assert extract_group_id("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)") is None
    assert extract_group_id("") is None
    assert extract_group_id(None) is None


def test_titulo_sin_markdown_pasa_igual():
    assert extract_title("GCC ME 1 (MON-10:00) (8-9 yo)") == "GCC ME 1 (MON-10:00) (8-9 yo)"


# ---------------------------------------------------------------------------
# Parseo del titulo
# ---------------------------------------------------------------------------


def test_parseo_del_titulo_real():
    spec = parse_group_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert spec.branch == "GCC"
    assert spec.branch_id == BRANCH_IDS["GCC"] == 12
    assert spec.course_code == "ME"
    assert spec.age_min == 8 and spec.age_max == 9
    assert len(spec.sessions) == 1
    assert spec.sessions[0].weekday == 5  # sabado
    assert (spec.sessions[0].hour, spec.sessions[0].minute) == (16, 0)
    assert spec.warnings == []


def test_parseo_de_titulo_con_dos_sesiones():
    spec = parse_group_title("GCC PY Premium 12 (MON-10:00) (WED-10:00) (10-12 yo)")
    assert len(spec.sessions) == 2
    assert [s.weekday for s in spec.sessions] == [0, 2]


def test_parseo_avisa_si_falta_el_horario():
    spec = parse_group_title("GCC ME S-Premium 69 (8-9 yo)")
    assert spec.sessions == []
    assert any("dia y la hora" in w for w in spec.warnings)


def test_parseo_avisa_si_la_sucursal_es_desconocida():
    spec = parse_group_title("XXX ME 1 (SAT-16:00) (8-9 yo)")
    assert spec.branch_id is None
    assert any("sucursal" in w.lower() for w in spec.warnings)


def test_parseo_rechaza_hora_imposible():
    spec = parse_group_title("GCC ME 1 (SAT-99:00) (8-9 yo)")
    assert spec.sessions == []


# ---------------------------------------------------------------------------
# Resolucion via API
# ---------------------------------------------------------------------------


def test_resolucion_via_api_trae_todo(resolver):
    spec = resolver.from_api(72219)
    assert spec.source == "api"
    assert spec.group_id == 72219
    assert spec.course_id == 2014
    assert spec.course_name == "Minecraft. Secret Level 1"
    assert spec.duration_minutes == 60
    assert spec.timezone == "Asia/Riyadh"
    assert spec.branch_id == 12 and spec.branch_title == "GCC"
    assert spec.language == "Arabic"
    assert spec.age_min == 8 and spec.age_max == 9
    assert spec.ready is True


def test_el_default_teacher_marca_grupo_sin_asignar(resolver):
    spec = resolver.from_api(72219)
    assert spec.current_teacher_id == UNASSIGNED_TEACHER_ID
    assert spec.unassigned is True


def test_el_cruce_con_las_clases_reales_no_alerta_si_todo_cuadra(resolver):
    # 16:00 Riyadh == 13:00 UTC, que es lo que dice schedule_view.
    spec = resolver.from_api(72219)
    assert not any("no coincide con las clases reales" in w for w in spec.warnings), spec.warnings


def test_el_cruce_alerta_si_la_zona_del_grupo_esta_mal():
    # Si el grupo declarara Bogota en vez de Riyadh, 16:00 local serian 21:00 UTC
    # y no coincidiria con las 13:00Z reales. Eso debe avisarse.
    bad = dict(GROUP_72219)
    bad["group_timezone"] = {"name": "America/Bogota", "offset_utc": "UTC-05:00"}
    spec = GroupResolver(FakeClient(group=bad)).from_api(72219)
    assert any("no coincide con las clases reales" in w for w in spec.warnings)


def test_grupo_archivado_avisa():
    archived = dict(GROUP_72219)
    archived["group_is_archive"] = True
    spec = GroupResolver(FakeClient(group=archived)).from_api(72219)
    assert any("archivado" in w for w in spec.warnings)


def test_sin_lesson_length_cae_en_la_duracion_del_curso():
    no_len = dict(GROUP_72219)
    no_len["lesson_length"] = None
    spec = GroupResolver(FakeClient(group=no_len)).from_api(72219)
    assert spec.duration_minutes == 60  # del titulo del curso: [60 min]
    assert any("lesson_length" in w for w in spec.warnings)


def test_resolve_prefiere_el_enlace_sobre_el_titulo(resolver):
    spec = resolver.resolve(
        link="https://bo.kodland.org/groups/72219",
        title="GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)",
    )
    assert spec.source == "api"
    assert spec.course_id == 2014


def test_resolve_avisa_si_el_nombre_pegado_no_coincide(resolver):
    spec = resolver.resolve(
        link="https://bo.kodland.org/groups/72219",
        title="GCC PY Otro Grupo 11 (MON-09:00) (10-12 yo)",
    )
    assert any("no coincide" in w for w in spec.warnings)
    assert spec.course_id == 2014  # gana el API


# ---------------------------------------------------------------------------
# Resolucion via titulo
# ---------------------------------------------------------------------------


def test_el_codigo_solo_no_determina_el_curso(resolver):
    # "ME" + 8-9 lo usan tres cursos distintos en GCC (2014, 1847, 2007).
    # El sistema NO debe elegir por su cuenta.
    spec = resolver.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert spec.source == "title"
    assert spec.course_id is None, "con varios cursos posibles no debe adivinar"
    assert spec.ready is False
    ids = [o["id"] for o in spec.course_options]
    assert ids[0] == 2014, "el mas usado va primero"
    assert set(ids) == {2014, 1847, 2007}
    assert any("mas de un curso" in w for w in spec.warnings)


def test_las_opciones_traen_cuantos_grupos_usan_cada_curso(resolver):
    spec = resolver.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    por_id = {o["id"]: o["groups"] for o in spec.course_options}
    assert por_id == {2014: 9, 1847: 3, 2007: 1}


def test_cuando_hay_un_solo_curso_se_resuelve_solo(resolver):
    # PY 13-17 y DC 8-9 son inequivocos en los datos reales.
    for titulo, course_id, duracion in [
        ("GCC PY L-Premium 10 (SAT-14:00) (13-17 yo)", 1844, 90),
        ("GCC DC S-Premium 35 (SAT-12:00) (8-9 yo)", 2038, 60),
        ("GCC RO L-Premium 5 (THU-17:00) (10-12 yo)", 1846, 90),
    ]:
        spec = resolver.from_title(titulo)
        assert spec.course_id == course_id, titulo
        assert spec.duration_minutes == duracion, titulo
        assert spec.ready is True, titulo


def test_la_duracion_sale_del_curso_elegido(resolver):
    # RO 8-9 son 60 min y RO 10-12 son 90, con el mismo nombre de curso.
    corto = resolver.from_title("GCC RO L-Premium 5 (THU-17:00) (10-12 yo)")
    assert corto.duration_minutes == 90
    # (RO 8-9 tiene dos cursos, asi que no se resuelve solo; se comprueba la opcion)
    amb = resolver.from_title("GCC RO S-Premium 2 (SAT-11:00) (8-9 yo)")
    assert amb.course_options[0]["id"] == 1845
    assert amb.course_options[0]["default_duration"] == 60


def test_la_edad_del_titulo_puede_no_coincidir_con_la_del_curso(resolver):
    # PY 13-17 apunta a un curso declarado [12-17]: el solape lo resuelve.
    spec = resolver.from_title("GCC PY L-Premium 9 (TUE-17:00) (13-17 yo)")
    assert spec.course_id == 1844
    assert spec.course_options[0]["age_range"] == "12-17"


def test_un_codigo_sin_grupos_previos_cae_en_el_catalogo(resolver):
    # "SC" no lo usa ningun grupo: se busca por texto en el catalogo.
    spec = resolver.from_title("GCC SC S-Premium 1 (SAT-11:00) (8-9 yo)")
    assert spec.source == "title"
    assert spec.course_id is None or spec.course_id in {1862}


def test_si_no_se_pueden_leer_los_grupos_usa_el_respaldo_y_avisa():
    # Sin acceso a los grupos reales queda la tabla fija, que puede estar vieja.
    r = GroupResolver(FakeClient(group_rows=False))
    spec = r.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert spec.course_id == 1847  # el valor de la tabla fija
    assert any("desactualizada" in w for w in spec.warnings)


def test_el_respaldo_avisa_si_el_curso_ya_no_esta_en_el_catalogo():
    sin_1847 = [c for c in GCC_COURSES if c["id"] != 1847]
    spec = GroupResolver(FakeClient(courses=sin_1847, group_rows=False)).from_title(
        "GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)"
    )
    assert spec.course_id == 1847
    assert any("no aparece en el catalogo" in w for w in spec.warnings)


# ---------------------------------------------------------------------------
# Variantes del titulo encontradas en datos reales
# ---------------------------------------------------------------------------


def test_sucursal_con_guion_gcc_eng(resolver):
    # "GCC-ENG" son grupos de GCC dictados en ingles. El parser anterior no los
    # reconocia y quedaban sin sucursal.
    spec = resolver.from_title("GCC-ENG DC L-Premium 68 (FRI-15:00) (10-12 yo)")
    assert spec.branch_title == "GCC-ENG"
    assert spec.branch_id == BRANCH_IDS["GCC"]
    assert spec.timezone == "Asia/Riyadh"
    assert any("idioma" in w.lower() for w in spec.warnings)
    # Hereda el catalogo de GCC: DC 10-12 lo usan dos cursos, el 2039 domina.
    assert spec.course_options[0]["id"] == 2039


def test_sufijo_disbanded_avisa(resolver):
    spec = resolver.from_title("GCC RO S-Premium 1 (WED-17:00) (8-9 yo)_disbanded")
    assert any("baja" in w or "disbanded" in w for w in spec.warnings)
    # El sufijo no debe estorbar la lectura del resto del titulo.
    assert spec.sessions[0].label == "miercoles 17:00"
    assert spec.course_options[0]["id"] == 1845


def test_hora_y_media(resolver):
    spec = resolver.from_title("GCC DC L-Premium 72 (SUN-15:30) (10-12 yo)")
    assert (spec.sessions[0].hour, spec.sessions[0].minute) == (15, 30)
    assert spec.sessions[0].weekday == 6  # domingo


def test_tamano_inconsistente_con_la_edad_avisa(resolver):
    # S deberia ser 8-9. Si dice S con 10-12, el titulo esta mal armado.
    spec = resolver.from_title("GCC DC S-Premium 99 (SAT-12:00) (10-12 yo)")
    assert any("S-" in w or "esperado" in w for w in spec.warnings)


def test_via_titulo_saca_bien_dia_hora_y_edad(resolver):
    spec = resolver.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert spec.sessions[0].weekday == 5
    assert (spec.sessions[0].hour, spec.sessions[0].minute) == (16, 0)
    assert spec.age_min == 8 and spec.age_max == 9
    assert spec.branch_id == 12


def test_via_titulo_filtra_por_edad(resolver):
    # Edades 10-11 no deben traer Secret Level 1 (8-9).
    spec = resolver.from_title("GCC ME Premium 70 (SAT-16:00) (10-11 yo)")
    ids = {o["id"] for o in spec.course_options}
    assert 2006 in ids
    assert 2014 not in ids


def test_via_titulo_sin_coincidencias_avisa(resolver):
    spec = resolver.from_title("GCC ZZ Premium 1 (SAT-16:00) (8-9 yo)")
    assert spec.course_options == []
    assert spec.course_id is None
    assert any("Ningun curso" in w for w in spec.warnings)


def test_via_titulo_asume_la_zona_de_gcc(resolver):
    # El titulo no declara zona horaria. Si se dejara en UTC, "16:00" se
    # interpretaria como 16:00 UTC en vez de 16:00 Riyadh: 3 horas de error, y el
    # resultado igual saldria con candidatos que parecen validos.
    spec = resolver.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)")
    assert spec.timezone == "Asia/Riyadh"
    assert any("Riyadh" in w for w in spec.warnings)


def test_via_titulo_no_adivina_zona_de_sucursal_desconocida(resolver):
    spec = resolver.from_title("LATAM PY Premium 3 (MON-10:00) (10-12 yo)")
    assert spec.timezone is None
    assert any("zona horaria" in w for w in spec.warnings)
    assert spec.ready is False


def test_resolve_sin_nada_es_error(resolver):
    with pytest.raises(ValueError):
        resolver.resolve(link=None, title=None)


# ---------------------------------------------------------------------------
# Enlaces al BackOffice
# ---------------------------------------------------------------------------


def test_url_del_perfil_del_tutor():
    from groups import teacher_url

    assert teacher_url(3460750) == "https://bo.kodland.org/teachers/3460750"
    assert teacher_url("3460750") == "https://bo.kodland.org/teachers/3460750"
    assert teacher_url(None) is None
    assert teacher_url("") is None


def test_url_del_grupo():
    from groups import group_url

    assert group_url(72219) == "https://bo.kodland.org/groups/72219"
    assert group_url(None) is None


def test_el_grupo_resuelto_trae_sus_enlaces(resolver):
    spec = resolver.from_api(72219).as_dict()
    assert spec["group_url"] == "https://bo.kodland.org/groups/72219"
    # El tutor actual es la cuenta centinela, pero el enlace se arma igual.
    assert spec["current_teacher_url"] == "https://bo.kodland.org/teachers/245461"


def test_el_enlace_del_grupo_hace_ida_y_vuelta():
    # Lo que se muestra tiene que poder pegarse de nuevo en la app.
    from groups import extract_group_id, group_url

    assert extract_group_id(group_url(72219)) == 72219


def test_un_grupo_sin_id_no_inventa_enlace(resolver):
    spec = resolver.from_title("GCC ME S-Premium 69 (SAT-16:00) (8-9 yo)").as_dict()
    assert spec["group_url"] is None

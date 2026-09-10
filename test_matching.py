"""Casos de prueba del motor de matching.

Cubren los puntos donde este tipo de logica suele fallar en silencio:
conversion de zona horaria, bloques que envuelven el fin de semana, clases que
solo encajan parcialmente en la disponibilidad, y cruces con clases contiguas.
"""

from datetime import date

import pytest

from matching import (
    WEEK_MINUTES,
    Block,
    GroupRequest,
    Reason,
    Tutor,
    block_from_local,
    evaluate_tutor,
    find_candidates,
    format_minute_of_week,
    local_to_utc_minute_of_week,
    make_intervals,
    merge_intervals,
    resolve_offset_minutes,
)

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
REF = date(2026, 8, 17)  # lunes


# ---------------------------------------------------------------------------
# Conversion horaria
# ---------------------------------------------------------------------------


def test_conversion_offset_fijo():
    # Lunes 09:00 en UTC-5 son las 14:00 UTC del mismo lunes.
    mow = local_to_utc_minute_of_week(MON, 9, 0, utc_offset_minutes=-300)
    assert mow == 14 * 60
    assert format_minute_of_week(mow) == "lunes 14:00"


def test_conversion_envuelve_fin_de_semana():
    # Domingo 21:00 en UTC-5 cae en lunes 02:00 UTC: cruza el limite de la semana.
    mow = local_to_utc_minute_of_week(SUN, 21, 0, utc_offset_minutes=-300)
    assert format_minute_of_week(mow) == "lunes 02:00"


def test_conversion_ida_y_vuelta():
    mow = local_to_utc_minute_of_week(WED, 18, 30, utc_offset_minutes=-300)
    assert format_minute_of_week(mow, -300) == "miercoles 18:30"


def test_offset_iana_respeta_horario_de_verano():
    # Madrid en agosto es CEST (UTC+2); en enero es CET (UTC+1).
    verano = resolve_offset_minutes("Europe/Madrid", MON, 12, 0, date(2026, 8, 17))
    invierno = resolve_offset_minutes("Europe/Madrid", MON, 12, 0, date(2026, 1, 12))
    assert verano == 120
    assert invierno == 60


def test_bogota_no_tiene_horario_de_verano():
    for ref in (date(2026, 1, 12), date(2026, 8, 17)):
        assert resolve_offset_minutes("America/Bogota", MON, 12, 0, ref) == -300


# ---------------------------------------------------------------------------
# Aritmetica de intervalos
# ---------------------------------------------------------------------------


def test_intervalo_que_envuelve_se_parte_en_dos():
    piezas = make_intervals(WEEK_MINUTES - 30, 60)
    assert piezas == [(WEEK_MINUTES - 30, WEEK_MINUTES), (0, 30)]


def test_merge_une_contiguos():
    assert merge_intervals([(0, 60), (60, 120), (200, 240)]) == [(0, 120), (200, 240)]


def test_duracion_invalida():
    with pytest.raises(ValueError):
        make_intervals(0, 0)


# ---------------------------------------------------------------------------
# Filtros duros
# ---------------------------------------------------------------------------


def _tutor(**kwargs) -> Tutor:
    base = dict(
        tutor_id="t1",
        name="Tutor Prueba",
        courses={"python"},
        utc_offset_minutes=-300,
        timezone="America/Bogota",
        max_groups=5,
    )
    base.update(kwargs)
    return Tutor(**base)


def _request(**kwargs) -> GroupRequest:
    base = dict(
        course="python",
        weekday=MON,
        hour=9,
        minute=0,
        duration_minutes=60,
        utc_offset_minutes=-300,
        reference_date=REF,
    )
    base.update(kwargs)
    return GroupRequest(**base)


def test_tutor_disponible_y_libre_es_elegible():
    tutor = _tutor(
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)]
    )
    res = evaluate_tutor(tutor, _request())
    assert res.eligible
    assert res.blockers == []


def test_curso_no_dictado_bloquea():
    tutor = _tutor(
        courses={"scratch"},
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)],
    )
    res = evaluate_tutor(tutor, _request())
    assert not res.eligible
    assert Reason.COURSE_NOT_TAUGHT in res.blockers


def test_bloque_que_solo_encaja_parcial_no_sirve():
    # Tutor disponible 09:00-10:00; el grupo dura 90 minutos desde las 09:00.
    tutor = _tutor(
        availability=[block_from_local(MON, 9, 0, 60, utc_offset_minutes=-300)]
    )
    res = evaluate_tutor(tutor, _request(duration_minutes=90))
    assert not res.eligible
    assert Reason.NOT_AVAILABLE in res.blockers


def test_disponibilidad_partida_no_cubre_un_bloque_a_caballo():
    # Disponible 08:00-09:00 y 10:00-12:00. Un grupo 09:00-10:00 NO encaja
    # aunque este "entre" dos bloques disponibles.
    tutor = _tutor(
        availability=[
            block_from_local(MON, 8, 0, 60, utc_offset_minutes=-300),
            block_from_local(MON, 10, 0, 120, utc_offset_minutes=-300),
        ]
    )
    res = evaluate_tutor(tutor, _request())
    assert Reason.NOT_AVAILABLE in res.blockers


def test_cruce_con_clase_existente():
    tutor = _tutor(
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)],
        scheduled=[
            block_from_local(
                MON, 9, 30, 60, utc_offset_minutes=-300, label="Python Basico G-102"
            )
        ],
    )
    res = evaluate_tutor(tutor, _request())
    assert not res.eligible
    assert Reason.SCHEDULE_CONFLICT in res.blockers
    assert res.conflicts == ["Python Basico G-102"]


def test_clases_contiguas_no_se_cruzan_sin_colchon():
    # Clase existente 10:00-11:00, grupo nuevo 09:00-10:00: se tocan, no se cruzan.
    tutor = _tutor(
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)],
        scheduled=[block_from_local(MON, 10, 0, 60, utc_offset_minutes=-300)],
    )
    assert evaluate_tutor(tutor, _request()).eligible


def test_colchon_entre_clases_bloquea_contiguas():
    tutor = _tutor(
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)],
        scheduled=[block_from_local(MON, 10, 0, 60, utc_offset_minutes=-300)],
    )
    res = evaluate_tutor(tutor, _request(buffer_minutes=15))
    assert not res.eligible
    assert Reason.SCHEDULE_CONFLICT in res.blockers


def test_sin_cupo_bloquea():
    tutor = _tutor(
        max_groups=2,
        availability=[block_from_local(MON, 8, 0, 480, utc_offset_minutes=-300)],
        scheduled=[
            block_from_local(MON, 14, 0, 60, utc_offset_minutes=-300),
            block_from_local(MON, 15, 0, 60, utc_offset_minutes=-300),
        ],
    )
    res = evaluate_tutor(tutor, _request())
    assert Reason.AT_CAPACITY in res.blockers


def test_tutor_inactivo_bloquea():
    tutor = _tutor(
        active=False,
        availability=[block_from_local(MON, 8, 0, 240, utc_offset_minutes=-300)],
    )
    assert Reason.INACTIVE in evaluate_tutor(tutor, _request()).blockers


def test_sin_disponibilidad_declarada_no_bloquea_pero_avisa():
    tutor = _tutor(availability=[])
    res = evaluate_tutor(tutor, _request())
    assert res.eligible
    assert res.notes and "disponibilidad" in res.notes[0].lower()


def test_declaro_disponibilidad_pero_toda_reservada_si_bloquea():
    # Caso real: el tutor tiene franjas en el sistema, pero todas marcadas para
    # clase experta o extra, asi que ninguna sirve para un grupo regular.
    # No debe confundirse con "no declaro nada".
    tutor = _tutor(availability=[], availability_declared=True)
    res = evaluate_tutor(tutor, _request())
    assert not res.eligible
    assert Reason.NOT_AVAILABLE in res.blockers


# ---------------------------------------------------------------------------
# Zonas horarias cruzadas
# ---------------------------------------------------------------------------


def test_tutor_en_otra_zona_horaria_encaja():
    # Grupo lunes 09:00 Bogota (=14:00 UTC). Tutor en Madrid disponible
    # lunes 16:00-18:00 local (=14:00-16:00 UTC en agosto). Debe encajar.
    tutor = _tutor(
        timezone="Europe/Madrid",
        utc_offset_minutes=120,
        availability=[
            block_from_local(MON, 16, 0, 120, tz="Europe/Madrid", reference_date=REF)
        ],
    )
    res = evaluate_tutor(tutor, _request(timezone="America/Bogota", utc_offset_minutes=None))
    assert res.eligible, res.blockers


def test_tutor_en_otra_zona_horaria_no_encaja_por_una_hora():
    # Mismo caso pero el tutor solo esta disponible 15:00-16:00 Madrid
    # (=13:00-14:00 UTC): termina justo cuando empieza el grupo.
    tutor = _tutor(
        timezone="Europe/Madrid",
        utc_offset_minutes=120,
        availability=[
            block_from_local(MON, 15, 0, 60, tz="Europe/Madrid", reference_date=REF)
        ],
    )
    res = evaluate_tutor(tutor, _request(timezone="America/Bogota", utc_offset_minutes=None))
    assert Reason.NOT_AVAILABLE in res.blockers


def test_grupo_nocturno_que_cruza_el_limite_de_la_semana():
    # Grupo domingo 21:00 Bogota = lunes 02:00 UTC. El tutor declara domingo
    # 20:00-23:00 local. Si la aritmetica circular estuviera mal, esto fallaria.
    tutor = _tutor(
        availability=[block_from_local(SUN, 20, 0, 180, utc_offset_minutes=-300)]
    )
    res = evaluate_tutor(tutor, _request(weekday=SUN, hour=21))
    assert res.eligible, res.blockers


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ranking_prefiere_menos_carga():
    disponible = [block_from_local(MON, 6, 0, 720, utc_offset_minutes=-300)]
    cargado = _tutor(
        tutor_id="cargado",
        name="Cargado",
        availability=disponible,
        max_groups=5,
        scheduled=[
            block_from_local(MON, 14, 0, 60, utc_offset_minutes=-300),
            block_from_local(MON, 15, 0, 60, utc_offset_minutes=-300),
            block_from_local(MON, 16, 0, 60, utc_offset_minutes=-300),
        ],
    )
    liviano = _tutor(
        tutor_id="liviano",
        name="Liviano",
        availability=disponible,
        max_groups=5,
        scheduled=[],
    )
    res = find_candidates([cargado, liviano], _request())
    assert [c["tutor_id"] for c in res["eligible"]] == ["liviano", "cargado"]


def test_descartados_traen_motivo_y_los_casi_primero():
    disponible = [block_from_local(MON, 6, 0, 720, utc_offset_minutes=-300)]
    casi = _tutor(tutor_id="casi", name="Casi", courses={"scratch"}, availability=disponible)
    lejos = _tutor(
        tutor_id="lejos",
        name="Lejos",
        courses={"scratch"},
        active=False,
        availability=[],
    )
    res = find_candidates([lejos, casi], _request())
    assert res["summary"] == {"evaluated": 2, "eligible": 0, "rejected": 2}
    assert res["rejected"][0]["tutor_id"] == "casi"
    assert "no_dicta_el_curso" in res["rejected"][0]["blockers"]
    assert res["rejected"][0]["blocker_labels"]

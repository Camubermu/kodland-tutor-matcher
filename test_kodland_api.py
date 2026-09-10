"""Pruebas del adaptador, con payloads copiados del API real.

Los valores esperados de zona horaria se derivaron del tutor 537726
(``Asia/Singapore``), el mismo con el que se verifico empiricamente que la
grilla de ``availability_summary`` esta en UTC.
"""

import ssl
from datetime import date

from kodland_api import (
    GRID_ASSUMED_LESSON_MINUTES,
    SENTINEL_TEACHER_IDS,
    KodlandAdapter,
    build_ssl_context,
    _availability_block,
    _scheduled_block,
    _slot_to_mow,
    _within_circular,
    parse_course_title,
)
from matching import DAY_MINUTES, WEEK_MINUTES, format_minute_of_week

REF = date(2026, 8, 17)  # lunes
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


# ---------------------------------------------------------------------------
# Titulos de curso
# ---------------------------------------------------------------------------


def test_parse_titulo_scratch():
    c = parse_course_title(554, "[554] Scratch [2021][8-12][50m,60m][32L][Ind][Actual]")
    assert c.name == "Scratch"
    assert c.durations == [50, 60]
    assert c.default_duration == 50
    assert c.age_range == "8-12"
    assert c.region == "Ind"
    assert c.status == "Actual"


def test_parse_titulo_python_global():
    c = parse_course_title(717, "[717] Python Global [2021][12-15][60m,90m][32L][Ind][Actual]")
    assert c.name == "Python Global"
    assert c.durations == [60, 90]
    assert c.age_range == "12-15"


def test_parse_titulo_con_emoji_y_min_separado():
    c = parse_course_title(2106, "[2106]\U0001f3b6 Music Lab[2026][10-14][50 min][3 L][Italy][in progress]")
    assert "Music Lab" in c.name
    assert c.durations == [50]
    assert c.age_range == "10-14"
    assert c.region == "Italy"
    assert c.status == "in progress"


def test_parse_titulo_no_confunde_cantidad_de_lecciones_con_duracion():
    # "32L" son lecciones, no minutos: no debe entrar en durations.
    c = parse_course_title(738, "[738] Programista gier Roblox [2022][10-12][60m][32L][Poland][Archive]")
    assert c.durations == [60]
    assert c.status == "Archive"


def test_parse_titulo_sin_duracion_declarada_cae_en_60():
    c = parse_course_title(1, "[1] Curso raro")
    assert c.durations == []
    assert c.default_duration == 60


# ---------------------------------------------------------------------------
# Disponibilidad declarada -> UTC
# ---------------------------------------------------------------------------


def test_bloque_singapore_lunes_coincide_con_la_grilla_real():
    # Payload real del tutor 537726. Monday 08:00-21:00 en UTC+8 == Monday 00:00 UTC,
    # que es exactamente donde empieza a aparecer en availability_summary.
    slot = {
        "start_hour": "08:00:00",
        "end_hour": "21:00:00",
        "weekday": "Monday",
        "is_expert_lesson": True,
        "is_extra_lesson": True,
    }
    block = _availability_block(slot, MON, "Asia/Singapore", REF)
    assert block is not None
    assert block.start == 0
    assert block.duration == 13 * 60
    assert format_minute_of_week(block.start) == "lunes 00:00"


def test_bloque_singapore_miercoles_coincide_con_la_grilla_real():
    slot = {"start_hour": "09:00:00", "end_hour": "21:00:00", "weekday": "Wednesday"}
    block = _availability_block(slot, WED, "Asia/Singapore", REF)
    # Wed 09:00 SGT -> Wed 01:00 UTC. La grilla real arranca en Wednesday 01:00.
    assert format_minute_of_week(block.start) == "miercoles 01:00"
    assert block.duration == 12 * 60


def test_bloque_que_cruza_la_medianoche_local():
    slot = {"start_hour": "22:00:00", "end_hour": "02:00:00", "weekday": "Friday"}
    block = _availability_block(slot, FRI, "America/Bogota", REF)
    assert block.duration == 4 * 60
    assert format_minute_of_week(block.start, -300) == "viernes 22:00"


def test_bloque_madrid_usa_horario_de_verano_de_la_fecha_de_referencia():
    slot = {"start_hour": "16:00:00", "end_hour": "20:00:00", "weekday": "Monday"}
    verano = _availability_block(slot, MON, "Europe/Madrid", date(2026, 8, 17))
    invierno = _availability_block(slot, MON, "Europe/Madrid", date(2026, 1, 12))
    # CEST (UTC+2) vs CET (UTC+1): una hora de diferencia en UTC.
    assert (invierno.start - verano.start) % WEEK_MINUTES == 60


def test_bloque_invalido_devuelve_none():
    assert _availability_block({"start_hour": "x", "end_hour": "y"}, MON, "UTC", REF) is None


# ---------------------------------------------------------------------------
# Clases agendadas -> bloque semanal
# ---------------------------------------------------------------------------


def test_clase_agendada_real_se_proyecta_bien():
    # Payload real: 2026-08-17 es lunes; 07:00-08:30 UTC.
    entry = {
        "group_title": "onboarding_tutors_poland",
        "course_title": "[738] Programista gier Roblox [...]",
        "start_time": "2026-08-17T07:00:00Z",
        "end_time": "2026-08-17T08:30:00Z",
    }
    block = _scheduled_block(entry, "grupo")
    assert block.start == 7 * 60
    assert block.duration == 90
    assert "onboarding_tutors_poland" in block.label
    assert "lunes 07:00 UTC" in block.label


def test_clase_agendada_con_offset_no_utc_se_normaliza():
    entry = {"group_title": "G", "start_time": "2026-08-17T10:00:00+03:00", "end_time": "2026-08-17T11:00:00+03:00"}
    block = _scheduled_block(entry, "grupo")
    assert block.start == 7 * 60  # 10:00+03:00 == 07:00 UTC
    assert block.duration == 60


def test_clase_agendada_sin_end_time_asume_60():
    entry = {"group_title": "G", "start_time": "2026-08-17T07:00:00Z", "end_time": None}
    assert _scheduled_block(entry, "grupo").duration == 60


def test_clase_agendada_sin_start_time_se_descarta():
    assert _scheduled_block({"group_title": "G"}, "grupo") is None


# ---------------------------------------------------------------------------
# Helpers de la grilla
# ---------------------------------------------------------------------------


def test_slot_a_minuto_de_semana():
    assert _slot_to_mow(MON, "00:00") == 0
    assert _slot_to_mow(WED, "01:00") == 2 * DAY_MINUTES + 60
    assert _slot_to_mow(MON, "basura") is None


def test_ventana_circular_normal():
    assert _within_circular(100, 50, 150)
    assert not _within_circular(200, 50, 150)


def test_ventana_circular_que_envuelve_la_semana():
    # Ventana de domingo 23:30 a lunes 00:30.
    start = 6 * DAY_MINUTES + 23 * 60 + 30
    end = start + 60
    assert _within_circular(start + 10, start, end)
    assert _within_circular(10, start, end)  # ya es lunes
    assert not _within_circular(5000, start, end)


# ---------------------------------------------------------------------------
# Preseleccion via grilla
# ---------------------------------------------------------------------------


class _FakeClient:
    """Grilla sintetica que reproduce el recorte de 90 minutos del API real."""

    def __init__(self, grid, slots):
        self._grid = grid
        self._slots = slots
        self.calls = []

    def availability_grid(self, course_id=None, **filters):
        self.calls.append(("grid", course_id, filters))
        return {"weekdays": list(self._grid), "time_slots": self._slots, "grid": self._grid}


def _grid_with(teachers_by_slot):
    slots = sorted(teachers_by_slot)
    grid = {
        "Monday": {
            slot: {"count": len(teachers_by_slot[slot]), "teachers": teachers_by_slot[slot]}
            for slot in slots
        }
    }
    for day in ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        grid[day] = {slot: {"count": 0, "teachers": []} for slot in slots}
    return grid, slots


def _adapter(grid, slots):
    return KodlandAdapter(_FakeClient(grid, slots))


def test_preseleccion_encuentra_al_tutor_del_slot_exacto():
    t = {"id": 1, "full_name": "Ana", "groups_count": 3}
    grid, slots = _grid_with({"09:00": [t], "10:00": []})
    stubs, meta = _adapter(grid, slots).shortlist(717, 9 * 60, 90)
    assert [s["id"] for s in stubs] == [1]
    assert meta["window_widened_minutes"] == 0  # clase de 90 min: no hace falta ensanchar


def test_preseleccion_ensancha_la_ventana_para_clases_cortas():
    # El API recorta el ultimo slot asumiendo 90 min. Para una clase de 60 min
    # el tutor sigue sirviendo, y debe recuperarse mirando slots anteriores.
    t = {"id": 7, "full_name": "Ultimo slot", "groups_count": 0}
    grid, slots = _grid_with({"11:30": [t], "12:00": []})
    adapter = _adapter(grid, slots)

    # Se pide una clase de 60 min a las 12:00 UTC: la grilla no lista ese slot.
    stubs, meta = adapter.shortlist(717, 12 * 60, 60)
    assert meta["window_widened_minutes"] == GRID_ASSUMED_LESSON_MINUTES - 60
    assert [s["id"] for s in stubs] == [7], "el tutor del slot 11:30 debe entrar a la preseleccion"


def test_preseleccion_ignora_tutores_lejanos():
    lejano = {"id": 9, "full_name": "Lejano", "groups_count": 0}
    grid, slots = _grid_with({"03:00": [lejano], "09:00": []})
    stubs, _ = _adapter(grid, slots).shortlist(717, 12 * 60, 60)
    assert stubs == []


def test_preseleccion_excluye_la_cuenta_centinela():
    sentinel_id = next(iter(SENTINEL_TEACHER_IDS))
    grid, slots = _grid_with(
        {"09:00": [{"id": sentinel_id, "full_name": "------ default teacher", "groups_count": 1044}]}
    )
    stubs, _ = _adapter(grid, slots).shortlist(717, 9 * 60, 90)
    assert stubs == []


def test_preseleccion_conserva_el_groups_count_mayor():
    grid, slots = _grid_with(
        {
            "09:00": [{"id": 5, "full_name": "Ana", "groups_count": 2}],
            "09:30": [{"id": 5, "full_name": "Ana", "groups_count": 4}],
        }
    )
    stubs, _ = _adapter(grid, slots).shortlist(717, 9 * 60, 90)
    assert len(stubs) == 1 and stubs[0]["groups_count"] == 4


def test_preseleccion_pasa_el_curso_al_api():
    grid, slots = _grid_with({"09:00": []})
    client = _FakeClient(grid, slots)
    KodlandAdapter(client).shortlist(717, 9 * 60, 60)
    assert client.calls[0][1] == 717


# ---------------------------------------------------------------------------
# Contexto TLS
# ---------------------------------------------------------------------------


def test_por_defecto_verifica_el_certificado(monkeypatch):
    monkeypatch.delenv("KODLAND_CA_BUNDLE", raising=False)
    monkeypatch.delenv("KODLAND_INSECURE", raising=False)
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_insecure_desactiva_la_verificacion_solo_si_se_pide(monkeypatch):
    monkeypatch.delenv("KODLAND_CA_BUNDLE", raising=False)
    monkeypatch.setenv("KODLAND_INSECURE", "1")
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_ca_bundle_se_usa_y_mantiene_la_verificacion(monkeypatch, tmp_path):
    # Si hay una CA corporativa declarada, se usa esa y se sigue verificando:
    # el atajo insecure no debe pisar una configuracion explicita y correcta.
    import ssl as _ssl

    bundle = tmp_path / "ca.pem"
    bundle.write_text(_ssl.get_default_verify_paths().cafile and
                      open(_ssl.get_default_verify_paths().cafile).read() or "")
    if not bundle.read_text().strip():
        import pytest as _pytest

        _pytest.skip("no hay bundle de CA del sistema para copiar en este entorno")

    monkeypatch.setenv("KODLAND_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("KODLAND_INSECURE", "1")
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "una CA explicita no debe caer en modo insecure"

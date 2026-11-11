from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _metric(at, prefix):
    return next(m.value for m in at.metric if m.label.startswith(prefix))


def test_app_runs_and_fleet_controls_change_plan():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert _metric(at, "Não atendidas") == "0"
    next(s for s in at.slider if s.label == "Veículos").set_value(1)
    at.run()
    assert not at.exception
    assert int(_metric(at, "Não atendidas")) > 0  # 1 veículo não dá conta das 20 entregas
    assert at.warning  # e o painel explica o que ficou de fora

import pytest

from predicciones.equipos import coincide, resolver


@pytest.mark.parametrize("nombre,candidato", [
    ("Manchester City", "Manchester United"),
    ("Inter", "Inter Miami CF"),
    ("Celta", "Celtic"),
    ("Atletico Madrid", "Atletico Mineiro"),
])
def test_un_candidato_distinto(nombre, candidato):
    assert resolver(nombre, [candidato]) is None
    assert not coincide(nombre, candidato)


@pytest.mark.parametrize("nombre,candidato", [
    ("America", "Club America"),
    ("Toluca", "Deportivo Toluca"),
    ("Dortmund", "Borussia Dortmund"),
    ("Manchester United", "Man United"),  # alias
    ("Atlético de Madrid", "Atletico Madrid"),
])
def test_un_candidato_igual(nombre, candidato):
    assert resolver(nombre, [candidato]) == candidato


def test_varios_candidatos():
    equipos = ["Manchester United", "Manchester City", "Liverpool", "Chelsea"]
    assert resolver("Manchester City", equipos) == "Manchester City"
    assert resolver("Liverpol", equipos) == "Liverpool"
    assert resolver("Manchester", equipos) is None  # ambiguo
    assert resolver("Equipo 1", [f"Equipo {i}" for i in range(12)]) == "Equipo 1"
    assert resolver("Recien Ascendido", [f"Equipo {i}" for i in range(12)]) is None

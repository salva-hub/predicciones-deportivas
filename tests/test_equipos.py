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


GRUPOS = [
    ["Pumas", "Pumas UNAM", "UNAM"],
    ["Chivas", "Guadalajara", "CD Guadalajara"],
    ["Roma", "AS Roma"],
    ["Leeds", "Leeds United"],
    ["Lyon", "Olympique Lyonnais"],
    ["PSG", "Paris Saint-Germain", "Paris SG"],
    ["Spurs", "Tottenham Hotspur", "Tottenham"],
    ["Internazionale", "Inter Milan", "Inter"],
    ["Bayern München", "Bayern Munich"],
    ["América", "Club América"],
    ["Cruz Azul", "Cruz Azul"],
    ["Tigres", "Tigres UANL"],
    ["Monterrey", "Rayados"],
    ["Atlas", "Atlas FC"],
    ["Santos", "Santos Laguna"],
    ["León", "Club León"],
    ["Pachuca", "CF Pachuca"],
    ["Toluca", "Deportivo Toluca"],
    ["Necaxa", "Club Necaxa"],
    ["Querétaro", "Queretaro FC"],
    ["Puebla", "Club Puebla"],
    ["Tijuana", "Xolos"],
    ["Juárez", "FC Juárez"],
    ["Mazatlán", "Mazatlan FC"],
    ["San Luis", "Atlético de San Luis"],
]
PARES = [(a, b) for g in GRUPOS for a in g for b in g if a != b]


@pytest.mark.parametrize("nombre,candidato", PARES)
def test_alias_un_candidato(nombre, candidato):
    assert coincide(nombre, candidato)
    assert coincide(candidato, nombre)
    assert resolver(nombre, [candidato]) == candidato


LIGA_MX = ["Club América", "Cruz Azul", "Pumas UNAM", "CD Guadalajara", "Tigres UANL", "Monterrey", "Atlas",
           "Santos Laguna", "Club León", "Pachuca", "Deportivo Toluca", "Necaxa", "Querétaro", "Puebla",
           "Club Tijuana", "FC Juárez", "Mazatlán FC", "Atlético de San Luis"]


@pytest.mark.parametrize("nombre,esperado", [
    ("Pumas", "Pumas UNAM"), ("UNAM", "Pumas UNAM"), ("Chivas", "CD Guadalajara"),
    ("Guadalajara", "CD Guadalajara"), ("América", "Club América"), ("Tigres", "Tigres UANL"),
    ("Rayados", "Monterrey"), ("Santos", "Santos Laguna"), ("León", "Club León"), ("Toluca", "Deportivo Toluca"),
    ("Xolos", "Club Tijuana"), ("Tijuana", "Club Tijuana"), ("Juárez", "FC Juárez"), ("Mazatlán", "Mazatlán FC"),
    ("San Luis", "Atlético de San Luis"), ("Queretaro", "Querétaro"), ("Cruz Azul", "Cruz Azul"),
])
def test_alias_liga_mx_varios_candidatos(nombre, esperado):
    assert resolver(nombre, LIGA_MX) == esperado


@pytest.mark.parametrize("nombre,esperado,lista", [
    ("Spurs", "Tottenham Hotspur", ["Tottenham Hotspur", "Leeds United", "Manchester City", "Manchester United"]),
    ("Leeds", "Leeds United", ["Tottenham Hotspur", "Leeds United", "Manchester City", "Manchester United"]),
    ("PSG", "Paris Saint-Germain", ["Paris Saint-Germain", "Olympique Lyonnais", "Paris FC"]),
    ("Lyon", "Olympique Lyonnais", ["Paris Saint-Germain", "Olympique Lyonnais", "Paris FC"]),
    ("Inter", "Internazionale", ["Internazionale", "AS Roma", "AC Milan"]),
    ("Roma", "AS Roma", ["Internazionale", "AS Roma", "AC Milan"]),
    ("Bayern München", "Bayern Munich", ["Bayern Munich", "Borussia Dortmund"]),
    ("Santos", "Santos", ["Santos", "Palmeiras", "Flamengo"]),  # Brasil: el Santos de su propia liga
])
def test_alias_otras_ligas(nombre, esperado, lista):
    assert resolver(nombre, lista) == esperado


@pytest.mark.parametrize("nombre,candidato", [
    ("Inter Milan", "Inter Miami CF"),
    ("Internazionale", "Inter Miami CF"),
    ("Man City", "Man United"),
    ("Celta Vigo", "Celtic"),
])
def test_alias_no_mezcla_equipos(nombre, candidato):
    assert not coincide(nombre, candidato)
    assert resolver(nombre, [candidato]) is None

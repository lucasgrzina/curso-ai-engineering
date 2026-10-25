"""Tests de las herramientas de los especialistas (app/agents/herramientas.py)."""

from __future__ import annotations

from app.agents.herramientas import buscar_en_politicas, calcular, calculadora, estadisticas


def test_buscar_en_politicas_encuentra_el_dato_sin_tildes():
    salida = buscar_en_politicas.invoke({"consulta": "dias de vacaciones antiguedad"})
    assert "14 días" in salida


def test_buscar_en_politicas_avisa_si_no_hay_resultados():
    assert "Sin resultados" in buscar_en_politicas.invoke({"consulta": "xyzzy"})


def test_calculadora_resuelve_la_cuenta_de_vacaciones():
    assert calcular("5*14 + 5*21 + 2*28") == 231


def test_calculadora_rechaza_codigo_arbitrario():
    salida = calculadora.invoke({"expresion": "__import__('os').system('echo hack')"})
    assert "Error" in salida


def test_calculadora_rechaza_exponentes_gigantes():
    assert "Error" in calculadora.invoke({"expresion": "9**9**9"})


def test_estadisticas_resume_la_lista():
    assert "suma=63" in estadisticas.invoke({"numeros": [14, 21, 28]})


def test_estadisticas_lista_vacia_devuelve_error():
    assert "Error" in estadisticas.invoke({"numeros": []})

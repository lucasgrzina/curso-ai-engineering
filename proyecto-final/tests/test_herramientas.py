"""Tests de las herramientas de cálculo del analista (app/agents/herramientas.py).

La herramienta del investigador (`buscar_en_politicas`) es RAG y se prueba aparte
en `test_rag.py` (con un índice en memoria y embeddings falsos, offline).
"""

from __future__ import annotations

from app.agents.herramientas import calcular, calculadora, estadisticas


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

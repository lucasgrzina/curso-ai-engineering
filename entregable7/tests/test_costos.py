"""Tests del cálculo de costo en USD a partir de tokens (app/costos.py)."""

from __future__ import annotations

import pytest

from app import costos


def test_normaliza_modelo_quita_prefijo_de_proveedor():
    assert costos._normalizar_modelo("openai/gpt-oss-20b") == "gpt-oss-20b"
    assert costos._normalizar_modelo("GPT-4o-Mini") == "gpt-4o-mini"


def test_precio_prefijo_mas_especifico_gana():
    # "gpt-4o-mini" debe ganarle a "gpt-4o" (prefijo más largo).
    precio, fuente = costos.precio_de_modelo("gpt-4o-mini-2024-07-18")
    assert fuente == "tabla"
    assert precio == (0.15, 0.60)


def test_modelo_desconocido_sin_precio():
    precio, fuente = costos.precio_de_modelo("modelo-inventado-xyz")
    assert precio is None
    assert fuente == "desconocido"


def test_costo_usd_calcula_desde_tokens():
    # 1.000.000 in * 0.15 + 1.000.000 out * 0.60 = 0.75 USD
    assert costos.costo_usd("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)


def test_override_por_entorno_fuerza_precio_de_cualquier_modelo(monkeypatch):
    monkeypatch.setenv("COSTO_USD_INPUT_POR_1M", "1.0")
    monkeypatch.setenv("COSTO_USD_OUTPUT_POR_1M", "2.0")
    precio, fuente = costos.precio_de_modelo("modelo-inventado-xyz")
    assert fuente == "override_env"
    assert precio == (1.0, 2.0)
    # 500k in * 1.0 + 250k out * 2.0 = 1.0 USD
    assert costos.costo_usd("lo-que-sea", 500_000, 250_000) == pytest.approx(1.0)


def test_resumen_costo_groq_ahora_tiene_usd():
    # El modelo de Groq que antes solo mostraba tokens ahora tiene costo en USD.
    uso = {"openai/gpt-oss-20b": {"input_tokens": 41113, "output_tokens": 18601, "total_tokens": 59714}}
    resumen = costos.resumen_de_costo(uso)
    assert resumen["total_tokens"] == 59714
    assert resumen["precio_fuente"] == "tabla"
    esperado = (41113 * 0.10 + 18601 * 0.50) / 1_000_000
    assert resumen["costo_usd"] == pytest.approx(round(esperado, 6))
    assert resumen["moneda"] == "USD"


def test_resumen_vacio_es_neutral():
    r = costos.resumen_de_costo({})
    assert r["total_tokens"] == 0
    assert r["costo_usd"] == 0.0
    assert r["precio_fuente"] == "sin_datos"


def test_resumen_parcial_cuando_un_modelo_no_tiene_precio():
    uso = {
        "gpt-4o-mini": {"input_tokens": 1000, "output_tokens": 1000, "total_tokens": 2000},
        "modelo-raro": {"input_tokens": 500, "output_tokens": 500, "total_tokens": 1000},
    }
    r = costos.resumen_de_costo(uso)
    assert r["precio_fuente"] == "parcial"
    assert r["costo_usd"] is not None  # suma lo que sí tiene precio
    assert r["modelos"]["modelo-raro"]["costo_usd"] is None
    assert r["total_tokens"] == 3000


def test_fusionar_suma_tokens_por_modelo():
    a = {"m": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    b = {"m": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}
    fus = costos.fusionar(a, b)
    assert fus["m"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}

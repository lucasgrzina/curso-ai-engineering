"""Herramientas de los especialistas (idéntico al Módulo 6).

Cada agente recibe SOLO las suyas (mínimo privilegio): el investigador no puede
calcular y el analista no puede buscar. El LLM elige la herramienta leyendo
únicamente el docstring.

Los fallos se devuelven como texto (no como excepción) para que vuelvan al
modelo como un mensaje más y pueda corregirse.
"""

from __future__ import annotations

import ast
import operator as op
import statistics
import unicodedata

from langchain_core.tools import tool

# "Fuente externa" simulada: las políticas internas de TechCorp (mismo corpus de
# las pre-entregas 3/4). La consigna admite una búsqueda simulada sobre la base
# de conocimiento; así el sistema corre sin Pinecone ni red.
POLITICAS: dict[str, str] = {
    "politica_vacaciones.txt": (
        "Política de Vacaciones - TechCorp. Los empleados con menos de 5 años de antigüedad "
        "acceden a 14 días corridos de vacaciones por año. Los empleados con 5 a 10 años "
        "acceden a 21 días. Los empleados con más de 10 años acceden a 28 días."
    ),
    "politica_teletrabajo.txt": (
        "Política de Teletrabajo - TechCorp. El esquema estándar es de 3 días de trabajo "
        "remoto y 2 días presenciales por semana. Soporte Técnico Nivel 1 y Recepción son "
        "100% presenciales."
    ),
    "politica_seguridad_informatica.txt": (
        "Política de Seguridad Informática - TechCorp. Las contraseñas deben renovarse cada "
        "90 días y usar 2FA obligatorio en todos los sistemas internos."
    ),
    "onboarding_nuevos_empleados.txt": (
        "Proceso de Onboarding - TechCorp. El proceso dura 4 semanas: inducción general, "
        "capacitación del rol, y cierre con Recursos Humanos."
    ),
}

_STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "en", "y", "a", "que", "es",
    "por", "con", "para", "del", "cuantos", "cuantas", "cual", "como",
}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def _palabras(texto: str) -> set[str]:
    limpio = "".join(c if c.isalnum() else " " for c in _normalizar(texto))
    return {p for p in limpio.split() if len(p) > 2 and p not in _STOPWORDS}


@tool
def buscar_en_politicas(consulta: str) -> str:
    """Busca en las políticas internas de TechCorp (vacaciones, teletrabajo, seguridad
    informática, onboarding). Usala para obtener reglas y números concretos de la empresa.
    Recibe una consulta en lenguaje natural (ej: 'días de vacaciones por antigüedad').
    Devuelve los fragmentos relevantes con su fuente, o un aviso si no hay coincidencias."""
    buscadas = _palabras(consulta)
    puntuados = []
    for fuente, texto in POLITICAS.items():
        coincidencias = len(buscadas & _palabras(texto + " " + fuente.replace("_", " ")))
        if coincidencias:
            puntuados.append((coincidencias, fuente, texto))
    if not puntuados:
        return "Sin resultados: ninguna política coincide con la consulta. Reformulá con otras palabras clave."
    puntuados.sort(reverse=True)
    return "\n\n".join(f"[Fuente: {fuente}] {texto}" for _, fuente, texto in puntuados[:2])


_OPERADORES = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
}


def _evaluar(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        izq, der = _evaluar(nodo.left), _evaluar(nodo.right)
        if isinstance(nodo.op, ast.Pow) and abs(der) > 100:
            raise ValueError("exponente demasiado grande")
        return _OPERADORES[type(nodo.op)](izq, der)
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand))
    raise ValueError("expresión no permitida (solo números y + - * / ** paréntesis)")


def calcular(expresion: str) -> float:
    """Evalúa aritmética parseando el AST: nunca `eval()` sobre texto de un LLM."""
    return _evaluar(ast.parse(expresion.strip(), mode="eval").body)


@tool
def calculadora(expresion: str) -> str:
    """Evalúa una expresión aritmética (+, -, *, /, **, paréntesis) con números ya conocidos.
    Ejemplo: '5*14 + 5*21 + 2*28'. Devuelve el resultado o un mensaje de error."""
    try:
        return f"Resultado: {calcular(expresion)}"
    except Exception as e:  # noqa: BLE001 - cualquier fallo vuelve al modelo como texto
        return f"Error al calcular '{expresion}': {e}"


@tool
def estadisticas(numeros: list[float]) -> str:
    """Calcula suma, promedio, mínimo y máximo de una lista de números ya conocidos.
    Ejemplo: [14, 21, 28]. Devuelve el resumen o un error si la lista está vacía."""
    if not numeros:
        return "Error: la lista está vacía."
    return (
        f"suma={sum(numeros)}, promedio={statistics.mean(numeros):.2f}, "
        f"min={min(numeros)}, max={max(numeros)}, n={len(numeros)}"
    )


HERRAMIENTAS_INVESTIGADOR = [buscar_en_politicas]
HERRAMIENTAS_ANALISTA = [calculadora, estadisticas]

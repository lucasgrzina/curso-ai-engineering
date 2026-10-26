"""Herramientas de los especialistas (mínimo privilegio).

Cada agente recibe SOLO las suyas: el investigador busca (no calcula) y el
analista calcula (no busca). El LLM elige la herramienta leyendo su docstring.

Novedad del proyecto final: `buscar_en_politicas` ya NO es una búsqueda simulada
sobre un diccionario. Es la **herramienta RAG real** del M3/M4: recupera
fragmentos de un índice vectorial (ChromaDB, embeddings locales) de forma
asíncrona (`await`). Así el conocimiento específico del sistema entra por RAG,
tal como pide la consigna.

Los fallos se devuelven como texto (no como excepción) para que vuelvan al modelo
como un mensaje más y pueda corregirse.
"""

from __future__ import annotations

import ast
import operator as op
import statistics

from langchain_core.tools import tool


@tool
async def buscar_en_politicas(consulta: str) -> str:
    """Busca en las políticas internas de TechCorp (vacaciones, teletrabajo, seguridad
    informática, onboarding) usando RAG sobre la base de conocimiento vectorial.
    Usala para obtener reglas y números concretos de la empresa. Recibe una consulta en
    lenguaje natural (ej: 'días de vacaciones por antigüedad'). Devuelve los fragmentos
    más relevantes con su fuente, o un aviso si no hay coincidencias."""
    # Import perezoso: evita cargar ChromaDB/embeddings salvo que la tool se use.
    from app.rag.vectorstore import buscar

    return await buscar(consulta)


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

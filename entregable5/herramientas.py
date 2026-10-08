"""Contrato de herramientas del agente: una "base de datos" simulada de pedidos.

El LLM decide qué herramienta usar leyendo ÚNICAMENTE el docstring, así que
cada uno dice qué hace, cuándo usarla, qué recibe y cómo falla. Hay dos tablas
separadas a propósito: el usuario habla de *nombres* pero los pedidos se
consultan por *ID*, lo que obliga a un razonamiento de al menos dos pasos
(nombre -> cliente_id -> pedidos).

Las herramientas devuelven JSON como texto. Los fallos se devuelven como
`{"error": ...}` (no como excepción) para que vuelvan al modelo como un
mensaje más y pueda corregirse o pedir una aclaración.
"""

from __future__ import annotations

import asyncio
import json
import unicodedata
from typing import Any, TypedDict

from langchain_core.tools import BaseTool, tool


class Pedido(TypedDict):
    id: str
    fecha: str
    monto: int


CLIENTES_DB: dict[str, int] = {
    "ana garcia": 102,
    "juan perez": 205,
    "maria lopez": 310,
}

PEDIDOS_DB: dict[int, list[Pedido]] = {
    102: [
        {"id": "P-1001", "fecha": "2026-01-15", "monto": 5000},
        {"id": "P-1002", "fecha": "2026-03-02", "monto": 4500},
        {"id": "P-1003", "fecha": "2026-05-20", "monto": 5000},
    ],  # 3 pedidos, total 14500
    205: [
        {"id": "P-2001", "fecha": "2026-02-10", "monto": 3200},
    ],
    310: [
        {"id": "P-3001", "fecha": "2026-01-05", "monto": 5000},
        {"id": "P-3002", "fecha": "2026-02-18", "monto": 6000},
        {"id": "P-3003", "fecha": "2026-04-01", "monto": 5500},
        {"id": "P-3004", "fecha": "2026-06-12", "monto": 6300},
        {"id": "P-3005", "fecha": "2026-07-30", "monto": 5000},
    ],  # 5 pedidos, total 27800
}


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y sin espacios sobrantes: 'María  López' == 'maria lopez'."""
    sin_acentos = unicodedata.normalize("NFD", texto)
    sin_acentos = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def _json(datos: dict[str, Any]) -> str:
    return json.dumps(datos, ensure_ascii=False)


@tool
async def buscar_cliente_por_nombre(nombre: str) -> str:
    """Busca el ID interno (cliente_id) de un cliente a partir de su NOMBRE COMPLETO
    (nombre y apellido). Usar esta herramienta SIEMPRE que el usuario mencione a un
    cliente por su nombre y necesites su cliente_id antes de poder consultar sus
    pedidos. Devuelve JSON con {"cliente_id": <int>} si lo encuentra, o con
    {"error": ...} si el nombre no coincide con ningún cliente registrado (en ese
    caso NO inventes un ID: pedile al usuario que confirme el nombre completo)."""
    await asyncio.sleep(0)  # punto de cesión: en producción sería una consulta de red/DB
    clave = _normalizar(nombre)
    if clave not in CLIENTES_DB:
        return _json({"error": f"No se encontró ningún cliente llamado '{nombre}'. Verificá que el nombre y apellido estén completos y bien escritos."})
    return _json({"cliente": nombre, "cliente_id": CLIENTES_DB[clave]})


@tool
async def buscar_pedidos(cliente_id: int) -> str:
    """Devuelve la CANTIDAD de pedidos y el MONTO TOTAL gastado por un cliente, dado
    su cliente_id NUMÉRICO (no su nombre: si solo tenés el nombre, primero usá
    buscar_cliente_por_nombre). Usar para preguntas del tipo "¿cuántos pedidos tuvo?"
    o "¿cuánto gastó en total?". Devuelve JSON con {"pedidos": <int>, "total": <int>}
    o con {"error": ...} si el cliente_id no existe."""
    await asyncio.sleep(0)
    if cliente_id not in PEDIDOS_DB:
        return _json({"error": f"No existe ningún cliente con id={cliente_id}."})
    pedidos = PEDIDOS_DB[cliente_id]
    return _json({"cliente_id": cliente_id, "pedidos": len(pedidos), "total": sum(p["monto"] for p in pedidos)})


@tool
async def buscar_ultimo_pedido(cliente_id: int) -> str:
    """Devuelve el ÚLTIMO pedido (el más reciente) de un cliente, dado su cliente_id
    NUMÉRICO: id del pedido, fecha y monto. Usar para preguntas como "¿cuál fue su
    último pedido?" o "¿cuándo compró por última vez?". Si no conocés el cliente_id,
    primero usá buscar_cliente_por_nombre (o reutilizá el ID si ya apareció antes en
    la conversación). Devuelve JSON con {"ultimo_pedido": {...}} o con {"error": ...}
    si el cliente_id no existe."""
    await asyncio.sleep(0)
    if cliente_id not in PEDIDOS_DB:
        return _json({"error": f"No existe ningún cliente con id={cliente_id}."})
    ultimo = max(PEDIDOS_DB[cliente_id], key=lambda p: p["fecha"])
    return _json({"cliente_id": cliente_id, "ultimo_pedido": ultimo})


HERRAMIENTAS: list[BaseTool] = [buscar_cliente_por_nombre, buscar_pedidos, buscar_ultimo_pedido]

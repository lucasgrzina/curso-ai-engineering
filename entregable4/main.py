"""Demo del recuperador híbrido.

Uso:
    python main.py                       # 3 consultas de ejemplo
    python main.py "¿qué es ERR_TLS_4022?"   # una consulta propia
    python main.py --modo bm25 ...       # aísla un recuperador: hibrido | vectorial | bm25
    python main.py --interactive         # chat interactivo

Requisito previo: haber subido el corpus con `python ingesta.py`.
"""

from __future__ import annotations

import argparse
import logging

from config import consola_utf8
from rag_system import RAGSystem

CONSULTAS_EJEMPLO = [
    "¿Qué significa el error ERR_TLS_4021?",  # término exacto: BM25 brilla
    "¿Cómo evito que un trabajo se ejecute dos veces al apagar un worker?",  # parafraseo: vectorial
    "¿Qué hacer si la API devuelve 429?",  # mezcla de ambos
]


def mostrar(rag: RAGSystem, consulta: str, modo: str) -> None:
    print(f"\n🔎 {consulta}  [{modo}]")
    for i, f in enumerate(rag.buscar(consulta, modo=modo), 1):
        extracto = f.contenido.replace("\n", " ")[:110]
        print(f"  {i}. [{f.fuente} · pág {f.pagina} · {f.categoria}] {extracto}...")


def main() -> int:
    consola_utf8()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Consulta el recuperador híbrido BM25 + Pinecone")
    ap.add_argument("consulta", nargs="?", help="Consulta puntual (si se omite, corre los ejemplos)")
    ap.add_argument("--modo", choices=["hibrido", "vectorial", "bm25"], default="hibrido")
    ap.add_argument("--interactive", action="store_true", help="Abre un chat interactivo")
    args = ap.parse_args()

    try:
        rag = RAGSystem()
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n{exc}")
        return 1

    if args.consulta:
        mostrar(rag, args.consulta, args.modo)
    elif not args.interactive:
        for consulta in CONSULTAS_EJEMPLO:
            mostrar(rag, consulta, args.modo)

    if args.interactive:
        print("\n💬 Modo interactivo — preguntá sobre Nimbus Queue ('salir' para terminar)")
        while True:
            try:
                consulta = input("\n🧑 Vos: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if consulta.lower() in ("salir", "exit", "quit", ""):
                break
            mostrar(rag, consulta, args.modo)
        print("👋 Listo, terminamos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

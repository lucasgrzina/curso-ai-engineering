"""Evaluación cuantitativa del recuperador: Precision@5 y Recall@5.

Uso:
    python evaluate.py              # evalúa el recuperador híbrido
    python evaluate.py --comparar   # además compara BM25-solo vs. vectorial-solo vs. híbrido
    python evaluate.py --k 3        # otro valor de k

Golden Set (`golden_set.json`): pares {"pregunta", "documento_id_esperado"} donde
la respuesta está, por construcción, en un documento conocido.

- **Recall@k**: ¿está el documento correcto entre los k recuperados? (0 o 1 por
  pregunta). Mide si el LLM recibiría el contexto necesario; recall bajo =>
  alucinaciones por falta de contexto.
- **Precision@k**: de los k chunks recuperados, qué fracción proviene del
  documento esperado. Mide cuánto ruido recibe el LLM; precisión baja => ruido.

Nota de interpretación: cada documento se parte en 3-4 chunks, y la respuesta
vive en uno o dos. Por eso la Precision@5 máxima alcanzable es menor a 100 %
(≈ chunks del documento / 5): importa más compararla entre estrategias que
leerla en valor absoluto. Ver la sección de métricas del README.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from config import TOP_K, consola_utf8
from rag_system import Modo, RAGSystem
from schemas import CasoGolden

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"


def cargar_golden_set(ruta: Path = GOLDEN_SET_PATH) -> list[CasoGolden]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [CasoGolden.model_validate(d) for d in datos]


def recall_at_k(esperado: str, recuperados: list[str], k: int) -> float:
    """1.0 si el documento esperado aparece entre los primeros k fuentes, 0.0 si no."""
    return 1.0 if esperado in recuperados[:k] else 0.0


def precision_at_k(esperado: str, recuperados: list[str], k: int) -> float:
    """Fracción de los k puestos ocupada por chunks del documento esperado."""
    if k <= 0:
        return 0.0
    return sum(1 for fuente in recuperados[:k] if fuente == esperado) / k


def evaluar(rag: RAGSystem, golden: list[CasoGolden], k: int = TOP_K, modo: Modo = "hibrido") -> dict:
    """Corre todo el Golden Set y devuelve el detalle por pregunta y los promedios."""
    detalle = []
    for caso in golden:
        fuentes = [f.fuente for f in rag.buscar(caso.pregunta, modo=modo)]
        detalle.append(
            {
                "pregunta": caso.pregunta,
                "esperado": caso.documento_id_esperado,
                "recuperados": fuentes,
                f"recall@{k}": recall_at_k(caso.documento_id_esperado, fuentes, k),
                f"precision@{k}": precision_at_k(caso.documento_id_esperado, fuentes, k),
            }
        )
    n = len(detalle)
    return {
        "modo": modo,
        "k": k,
        "detalle": detalle,
        f"recall@{k}": sum(d[f"recall@{k}"] for d in detalle) / n,
        f"precision@{k}": sum(d[f"precision@{k}"] for d in detalle) / n,
    }


def imprimir_reporte(reporte: dict) -> None:
    k = reporte["k"]
    print("=" * 88)
    print(f"Evaluación del recuperador — modo: {reporte['modo']} (k={k})")
    print("=" * 88)
    for d in reporte["detalle"]:
        estado = "OK " if d[f"recall@{k}"] == 1.0 else "FALLA"
        print(f"[{estado}] {d['pregunta']}")
        print(f"        esperado:    {d['esperado']}")
        print(f"        recuperados: {d['recuperados']}")
        print(f"        Recall@{k}: {d[f'recall@{k}']:.0%} | Precision@{k}: {d[f'precision@{k}']:.0%}")
    print("-" * 88)
    print(f"RECALL@{k} PROMEDIO:    {reporte[f'recall@{k}']:.1%}")
    print(f"PRECISION@{k} PROMEDIO: {reporte[f'precision@{k}']:.1%}")


def imprimir_comparacion(reportes: list[dict]) -> None:
    k = reportes[0]["k"]
    print("\n" + "=" * 88)
    print(f"Comparación de estrategias (k={k})")
    print("=" * 88)
    print(f"{'Modo':<12} {f'Recall@{k}':>12} {f'Precision@{k}':>16}")
    for r in reportes:
        print(f"{r['modo']:<12} {r[f'recall@{k}']:>12.1%} {r[f'precision@{k}']:>16.1%}")


def main() -> int:
    consola_utf8()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Evalúa Precision@k y Recall@k del recuperador")
    ap.add_argument("--k", type=int, default=TOP_K, help="Cantidad de resultados a considerar")
    ap.add_argument("--comparar", action="store_true", help="Compara BM25, vectorial e híbrido")
    args = ap.parse_args()

    try:
        golden = cargar_golden_set()
        rag = RAGSystem(k=args.k)
        reporte = evaluar(rag, golden, k=args.k)
        imprimir_reporte(reporte)
        if args.comparar:
            reportes = [evaluar(rag, golden, k=args.k, modo=m) for m in ("bm25", "vectorial", "hibrido")]
            imprimir_comparacion(reportes)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

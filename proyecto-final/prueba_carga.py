"""Prueba de carga: 5 peticiones concurrentes contra la API (punto 5 de la consigna).

Lanza N tareas en paralelo, hace polling de cada una y, si el grafo se pausa en
el HITL, la aprueba automáticamente para que la ejecución llegue a DONE. Al final
imprime latencias de extremo a extremo (incluida la p95) medidas del lado cliente.

La p95 y el costo por ejecución "oficiales" salen del dashboard (Phoenix/LangSmith),
que los calcula a partir de los tokens; estas métricas locales son un complemento.

    python prueba_carga.py
    python prueba_carga.py --n 5 --base-url http://localhost:8000 --no-approve

Mientras corre, abrí el dashboard (http://localhost:6006 para Phoenix) y sacá las
capturas: trazas por nodo, costo por ejecución y latencia p95. Van en /screenshots.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx

PREGUNTAS = [
    "Un empleado cumple 12 años en TechCorp. Confirmá en la política los días de vacaciones por "
    "antigüedad (14 los primeros 5 años, 21 los siguientes 5, 28 los últimos 2) y calculá el total acumulado.",
    "Si alguien trabajó 8 años, ¿cuántos días de vacaciones acumuló en total según la política? Mostrá la cuenta.",
    "Según la política, ¿cuántos días de vacaciones por año corresponden a 6 años de antigüedad y cuánto suman en 3 años?",
    "Calculá el total de días de vacaciones de un empleado con 11 años de antigüedad usando los tramos de la política.",
    "¿Cuántos días de vacaciones acumula en 10 años alguien de TechCorp? Confirmá los tramos y sumalos.",
]

TERMINALES = {"DONE", "FAILED"}


async def una_tarea(client: httpx.AsyncClient, idx: int, pregunta: str, aprobar: bool) -> dict:
    inicio = time.perf_counter()
    r = await client.post("/tasks", json={"pregunta": pregunta})
    r.raise_for_status()
    job_id = r.json()["job_id"]
    aprobada = False

    while True:
        await asyncio.sleep(0.5)
        estado = (await client.get(f"/tasks/{job_id}")).json()
        st = estado["estado"]
        if st == "AWAITING_APPROVAL" and aprobar and not aprobada:
            await client.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "comentario": "auto-aprobado (prueba de carga)"})
            aprobada = True
        elif st == "AWAITING_APPROVAL" and not aprobar:
            break  # dejamos la tarea pausada a propósito
        if st in TERMINALES:
            break
        if time.perf_counter() - inicio > 420:
            st = "TIMEOUT"
            break

    segundos = time.perf_counter() - inicio
    print(f"  tarea {idx}: {st} en {segundos:.1f}s (job {job_id[:8]})")
    return {"idx": idx, "job_id": job_id, "estado": st, "segundos": segundos}


async def main(base_url: str, n: int, aprobar: bool) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        try:
            salud = (await client.get("/health")).json()
            print(f"API OK -> {salud}\n")
        except Exception as e:  # noqa: BLE001
            print(f"No pude contactar la API en {base_url}: {e}")
            return

        print(f"Lanzando {n} peticiones concurrentes...")
        t0 = time.perf_counter()
        resultados = await asyncio.gather(*(una_tarea(client, i + 1, PREGUNTAS[i % len(PREGUNTAS)], aprobar) for i in range(n)))
        total = time.perf_counter() - t0

    tiempos = sorted(r["segundos"] for r in resultados)
    p95 = tiempos[max(0, round(0.95 * len(tiempos)) - 1)]
    ok = sum(1 for r in resultados if r["estado"] == "DONE")
    print("\n" + "=" * 60)
    print(f"Concurrencia: {n} | Terminadas DONE: {ok}/{n} | Tiempo total de la corrida: {total:.1f}s")
    print(f"Latencia extremo a extremo (cliente): min={tiempos[0]:.1f}s  "
          f"mediana={statistics.median(tiempos):.1f}s  p95={p95:.1f}s  max={tiempos[-1]:.1f}s")
    print("La p95 y el costo por ejecución oficiales están en el dashboard de observabilidad.")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--n", type=int, default=5, help="cantidad de peticiones concurrentes")
    parser.add_argument("--no-approve", action="store_true", help="dejar las tareas pausadas en el HITL (no aprobar)")
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.n, aprobar=not args.no_approve))

# Entregable 4 · Sistema RAG escalable en la nube con Pinecone

Módulo 4 — *Escalabilidad documental: RAG avanzado y Pinecone*.

Escala el RAG local del [Entregable 3](../entregable3) (ChromaDB) a la nube y lo
hace más preciso y **medible**: ingesta a un índice **Pinecone Serverless** con
metadata avanzada, un **recuperador híbrido** (BM25 + vectorial fusionados con
RRF) y un script de **evaluación** con Precision@5 y Recall@5 sobre un Golden Set.

El corpus es la documentación (ficticia) de **Nimbus Queue**, una plataforma de
colas de trabajos: 6 documentos Markdown con códigos de error (`ERR_TLS_4021`),
comandos y parámetros exactos — justo donde la búsqueda léxica aporta.

```python
from rag_system import RAGSystem

rag = RAGSystem()                                  # BM25 + Pinecone, top-5
for f in rag.buscar("¿Qué significa ERR_TLS_4021?"):
    print(f.fuente, f.pagina, f.contenido[:80])
```

**Embeddings 100% locales** (HuggingFace `all-MiniLM-L6-v2`, **384 dimensiones**),
igual que en el Entregable 3. Este módulo solo *recupera*: no llama a ningún LLM,
así que la **única credencial** es `PINECONE_API_KEY`.

---

## 1. Cómo replicar el índice (paso a paso)

```bash
cd entregable4
uv venv --python 3.12
uv pip install -r requirements.txt

cp .env.example .env         # completá PINECONE_API_KEY (gratis en app.pinecone.io)

python setup_index.py        # 1) crea el índice Serverless si no existe (idempotente)
python ingesta.py            # 2) chunking + embeddings + upsert por lotes
python main.py               # 3) demo del recuperador híbrido
python evaluate.py           # 4) Precision@5 / Recall@5 con el Golden Set
python evaluate.py --comparar   # (opcional) BM25 vs. vectorial vs. híbrido
python verificar.py          # criterios de aceptación (offline por defecto)
python verificar.py --real   # además valida el índice real en Pinecone
```

Sin `uv`: `python -m venv .venv` y `.venv/Scripts/pip install -r requirements.txt`
(`.venv/bin/pip` en Linux/macOS).

Qué se crea en Pinecone:

| Elemento | Valor |
|---|---|
| Índice | `nimbus-rag-hibrido` (configurable con `INDEX_NAME`) |
| Tipo | **Serverless**, `aws` / `us-east-1` |
| Dimensión | **384** (debe coincidir con el modelo de embeddings) |
| Métrica | `cosine` |
| Namespace | `nimbus-docs` |
| Vectores | 20 (6 documentos, de 3 a 4 chunks cada uno) |

Reejecutar `ingesta.py` **no duplica** vectores: los IDs son determinísticos
(`<fuente>::<n>`), así que el upsert sobrescribe. `python ingesta.py --reset`
vacía el namespace antes de subir. La primera ejecución descarga el modelo de
embeddings (~90 MB).

## 2. Variables de entorno

Viven en `.env` (ignorado por git); plantilla en `.env.example`.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `PINECONE_API_KEY` | **sí** | — | [app.pinecone.io](https://app.pinecone.io) (plan gratuito) |
| `INDEX_NAME` | no | `nimbus-rag-hibrido` | Nombre del índice Serverless |
| `PINECONE_NAMESPACE` | no | `nimbus-docs` | Namespace dentro del índice |
| `PINECONE_CLOUD` / `PINECONE_REGION` | no | `aws` / `us-east-1` | Dónde se crea el índice |
| `RAG_TOP_K` | no | `5` | Cantidad de fragmentos a devolver |

## 3. Diseño

```
entregable4/
├── data/              # corpus: 6 .md en 3 categorías (autenticacion, despliegue, operaciones)
├── config.py          # única fuente de verdad: modelo, dimensión, índice, namespace, chunking
├── schemas.py         # Fragmento (resultado) y CasoGolden (par del benchmark)
├── setup_index.py     # crea el índice Serverless si no existe y valida su dimensión
├── ingesta.py         # /data -> chunks -> embeddings -> batch upsert con metadata
├── rag_system.py      # RAGSystem: EnsembleRetriever(BM25 + Pinecone) -> top-5
├── evaluate.py        # Precision@k y Recall@k + comparación de estrategias
├── golden_set.json    # 5 preguntas con su documento_id_esperado
├── main.py            # demo / modo interactivo
└── verificar.py       # criterios de aceptación (offline por defecto)
```

### Setup del índice (`setup_index.py`)

Verifica con `pc.has_index()` y crea el índice **solo si falta**. Si ya existe,
valida que su dimensión y métrica coincidan con las del modelo y, si no,
falla de inmediato con un mensaje claro: así el *mismatch de dimensiones* (subir
vectores de 1536 a un índice de 768) no aparece recién en el primer upsert.
`config.EMBEDDING_DIM` se define una sola vez y se reutiliza en todos lados.

### Ingesta (`ingesta.py`)

- **Chunking en tokens**: `RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=700, chunk_overlap=100)`,
  dentro del rango de 500-800 tokens de la consigna (más chico pierde contexto;
  más grande diluye el embedding). `chunk_size` es un techo: de los 20 chunks
  reales, 17 miden entre 500 y 800 tokens; los otros 3 son la cola final de un
  documento, que por construcción es más corta que el resto.
- **Metadata avanzada con el texto adentro**: cada vector guarda `source`
  (ruta relativa, es el `documento_id`), `categoria` (la carpeta), `titulo`,
  `pagina` (posición del chunk en el documento), `total_paginas`, `chunk_id` y
  el **texto original** en `text`. Una sola consulta a Pinecone devuelve vector,
  texto y fuente, sin base relacional aparte. Los nombres están fijos en
  `construir_metadata` para evitar el *schema drift*.
- **Batch upsert de 100 vectores** en vez de vector a vector (menos overhead de red).
- **Namespace** dedicado, para no mezclar tipos de dato o entornos.

> Para `.md`/`.txt` no existe una "página" real como en un PDF; `pagina` es la
> posición del chunk dentro de su documento.

### Recuperador híbrido (`rag_system.py`)

```python
EnsembleRetriever(retrievers=[bm25, vectorial_pinecone], weights=[0.5, 0.5])
```

- **BM25** (léxico) es fuerte con identificadores exactos (`ERR_TLS_4021`,
  `nq_queue_depth`); **Pinecone** (semántico) con parafraseos y sinónimos.
- **Fusión por RRF**: `EnsembleRetriever` combina por *posición* en cada ranking,
  no por puntaje. Sumar a mano un BM25 (escala ~0-20) con un coseno (0-1) es el
  error #1 de la recuperación híbrida; RRF no requiere normalizar.
- **Tokenizador propio para BM25** (`tokenizar`): minúsculas, sin acentos y
  `\w+`. El `split()` por defecto deja la puntuación pegada (`` `ERR_TLS_4021`. ``
  ≠ `ERR_TLS_4021`) y rompería justo el caso donde BM25 más aporta; `\w+`
  conserva los guiones bajos, así que un código de error sigue siendo un token.
- El índice BM25 se construye en memoria con los **mismos chunks** que usa la
  ingesta (`preparar_chunks`), por lo que ambos recuperadores ven el mismo corpus.
- `RAGSystem.buscar(consulta, modo=...)` devuelve el top-5 como objetos
  `Fragmento`; `modo` (`hibrido` | `vectorial` | `bm25`) permite aislar un
  recuperador para comparar.

### Evaluación (`evaluate.py`)

Golden Set de 5 pares `{"pregunta", "documento_id_esperado"}` (`golden_set.json`),
validados con Pydantic. Para cada pregunta se recuperan 5 chunks y se calcula:

- **Recall@5**: ¿el documento esperado está entre los 5? (0 o 1; con un solo
  documento esperado por pregunta no hay término medio). Mide si el LLM recibiría
  el contexto necesario — recall bajo implica alucinaciones por falta de contexto.
- **Precision@5**: qué fracción de los 5 chunks viene del documento esperado.
  Mide el ruido que recibiría el LLM.

#### Cómo leer la Precision@5

Cada documento se parte en 3 o 4 chunks y la respuesta vive en uno o dos, así
que aunque la recuperación sea perfecta, no todos los 5 puestos pueden ser del
documento esperado: el **techo de Precision@5** es 80 % para los documentos de
4 chunks (`errores_tls`, `configuracion_workers`) y 60 % para los de 3; sobre
este Golden Set el techo promedio es **68 %**. Es una propiedad del corpus, no
un defecto del sistema. Por eso `--comparar` es más informativo que el valor
absoluto: lo relevante es cómo se mueve entre BM25, vectorial e híbrido.

## 4. Resultados

Corrida real sobre Pinecone (`python evaluate.py --comparar`, k=5, 5 preguntas):

| Modo | Recall@5 | Precision@5 |
|---|---|---|
| BM25 solo | 100 % | 40 % |
| Vectorial solo (Pinecone) | 100 % | 52 % |
| **Híbrido (RRF)** | **100 %** | **60 %** |

El techo de Precision@5 sobre este Golden Set es 68 % (ver arriba), así que el
híbrido recupera casi todo lo que el corpus permite. En las 5 preguntas el
documento correcto aparece entre los 5 resultados, y en 4 de las 5 ocupa el
puesto 1 (la excepción es la pregunta sobre `nq_queue_depth`).

**Lectura honesta:** el híbrido supera a cada recuperador por separado en
Precision (60 % vs. 52 % y 40 %), que es lo esperado: BM25 aporta los términos
exactos y el vectorial los parafraseos, y RRF se queda con lo mejor de ambos.
Aun así, el Recall@5 empata en 100 % en los tres modos: con 6 documentos y 5
preguntas el Golden Set es demasiado chico para separar las estrategias por
Recall, y las diferencias de Precision salen de solo 5 preguntas, por lo que
conviene tomarlas como indicativas y no como una medición estadística.

## 5. Ejemplo de ejecución real

Salida **real** contra Pinecone Serverless, sin editar. El log completo
(ingesta, evaluación, demo de consultas y verificación) está en
[`evidencia/ejecucion_real.txt`](./evidencia/ejecucion_real.txt).

```text
$ python ingesta.py --reset
2026-10-07 22:44:55,472 [INFO] setup_index: El índice 'nimbus-rag-hibrido' ya existe — no se vuelve a crear
2026-10-07 22:44:56,460 [INFO] ingesta: --reset: vaciando el namespace 'nimbus-docs'
2026-10-07 22:44:56,770 [INFO] ingesta: Documentos cargados: 6
2026-10-07 22:44:56,972 [INFO] ingesta: Fragmentos generados: 20 (a partir de 6 documentos)
2026-10-07 22:45:13,699 [INFO] ingesta: Upsert: 20/20 vectores

Ingesta lista: 20 vector(es) en el namespace 'nimbus-docs' del índice 'nimbus-rag-hibrido'.
```

```text
$ python evaluate.py --comparar
========================================================================================
Evaluación del recuperador — modo: hibrido (k=5)
========================================================================================
[OK ] ¿Qué significa el error ERR_TLS_4021 y cómo se soluciona?
        esperado:    autenticacion/errores_tls.md
        recuperados: ['autenticacion/errores_tls.md', 'autenticacion/errores_tls.md', 'despliegue/instalacion_docker.md', 'autenticacion/errores_tls.md', 'operaciones/limites_y_cuotas.md']
        Recall@5: 100% | Precision@5: 60%
[OK ] ¿Cada cuántos días vence por defecto un token de API?
        esperado:    autenticacion/tokens_api.md
        recuperados: ['autenticacion/tokens_api.md', 'autenticacion/tokens_api.md', 'autenticacion/tokens_api.md', 'operaciones/monitoreo_metricas.md', 'despliegue/configuracion_workers.md']
        Recall@5: 100% | Precision@5: 60%
[OK ] ¿Cuántas veces se reintenta un trabajo fallido antes de pasar a la dead letter queue?
        esperado:    despliegue/configuracion_workers.md
        recuperados: ['despliegue/configuracion_workers.md', 'despliegue/configuracion_workers.md', 'operaciones/monitoreo_metricas.md', 'despliegue/configuracion_workers.md', 'despliegue/configuracion_workers.md']
        Recall@5: 100% | Precision@5: 80%
[OK ] ¿Cuántas solicitudes por minuto permite el plan Estándar antes de responder ERR_RATE_5001?
        esperado:    operaciones/limites_y_cuotas.md
        recuperados: ['operaciones/limites_y_cuotas.md', 'operaciones/limites_y_cuotas.md', 'autenticacion/tokens_api.md', 'operaciones/limites_y_cuotas.md', 'autenticacion/errores_tls.md']
        Recall@5: 100% | Precision@5: 60%
[OK ] ¿Qué métrica indica la cantidad de trabajos pendientes por cola?
        esperado:    operaciones/monitoreo_metricas.md
        recuperados: ['despliegue/configuracion_workers.md', 'operaciones/monitoreo_metricas.md', 'despliegue/configuracion_workers.md', 'operaciones/limites_y_cuotas.md', 'operaciones/monitoreo_metricas.md']
        Recall@5: 100% | Precision@5: 40%
----------------------------------------------------------------------------------------
RECALL@5 PROMEDIO:    100.0%
PRECISION@5 PROMEDIO: 60.0%

========================================================================================
Comparación de estrategias (k=5)
========================================================================================
Modo             Recall@5      Precision@5
bm25               100.0%            40.0%
vectorial          100.0%            52.0%
hibrido            100.0%            60.0%
```

```text
$ python verificar.py --real        # sección 5
5. Pinecone real (requiere PINECONE_API_KEY e ingesta previa)
-------------------------------------------------------------
  [OK ] el índice es Serverless - {'serverless': {'cloud': 'aws', 'region': 'us-east-1'}}
  [OK ] la dimensión del índice coincide con el modelo - dim=384
  [OK ] el namespace contiene un vector por chunk - 20 vectores / 20 chunks
  [OK ] el híbrido sobre Pinecone tiene Recall@5 de 100% en el Golden Set - recall@5=100%
  [OK ] el texto y la metadata vuelven desde Pinecone (sin otra base) - autenticacion/errores_tls.md pág 1

========================================================================
Todos los criterios se cumplen.
```

## 6. Errores comunes que el entregable evita

| Error de la consigna | Cómo se evita acá |
|---|---|
| Mismatch de dimensiones | `EMBEDDING_DIM` único en `config.py`; `setup_index.py` valida la dimensión de un índice existente |
| Ignorar el namespace | Todo (upsert, consulta, conteo) usa `NAMESPACE` |
| Subestimar el chunking | 700 tokens / 100 de overlap, medidos en tokens y no en caracteres |
| Sumar scores de BM25 y coseno | `EnsembleRetriever` fusiona por RRF (posición, no puntaje) |
| Schema drift de metadata | Esquema fijo en `construir_metadata`; `verificar.py` comprueba que todos los chunks lo comparten |
| Duplicar vectores al reingestar | IDs determinísticos `<fuente>::<n>` -> upsert idempotente |
| Claves en el repo | `.env` ignorado por git + `.env.example` como plantilla |

## 7. Estado de la verificación

`python verificar.py` corre **sin red ni API key**: valida configuración y
contratos Pydantic, el chunking y el esquema de metadata, el batching, el
tokenizador y BM25 sobre códigos de error, la fusión del `EnsembleRetriever`
(con un recuperador vectorial simulado), las fórmulas de Precision@k / Recall@k
y la consistencia del Golden Set con el corpus. La sección 5 (`--real`) valida el
índice real —Serverless, dimensión, un vector por chunk— y corre la evaluación
sobre Pinecone; si no hay `PINECONE_API_KEY` o falta el índice, se saltea
(`SKIP`) sin marcar falla.

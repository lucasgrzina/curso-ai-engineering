# Imagen de la API del Intelligence System (Python 3.12).
FROM python:3.12-slim

WORKDIR /code

# Caché de HuggingFace dentro de la imagen: el modelo de embeddings se descarga
# UNA vez en el build (durante la ingesta) y queda horneado, así el contenedor
# arranca y consulta el RAG 100% offline.
ENV HF_HOME=/code/.hf_cache \
    PYTHONUNBUFFERED=1

# Dependencias primero (mejor caché de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código y base de conocimiento.
COPY app ./app
COPY data ./data
COPY verificar.py prueba_carga.py ./

# Ingesta del RAG en tiempo de build: descarga el modelo de embeddings y deja el
# índice ChromaDB persistido en /code/vectorstore, dentro de la imagen.
RUN python -m app.rag.ingesta

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Imagen de la API (Python 3.12).
FROM python:3.12-slim

WORKDIR /code

# Dependencias primero (mejor caché de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación.
COPY app ./app
COPY verificar.py prueba_carga.py ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

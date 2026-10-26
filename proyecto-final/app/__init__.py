"""API de producción del orquestador multi-agente (Entregable 7).

El objeto FastAPI vive en `app.main:app`. Mantené este `__init__` liviano para
evitar imports circulares (los submódulos hacen `from app import config`).
"""

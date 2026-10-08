# Monitoreo y métricas operativas

Nimbus Queue expone métricas en formato Prometheus en el endpoint `/metrics` del servicio `nq-api`, en el puerto 9102. Este endpoint requiere un token de lectura y puede integrarse con Prometheus, Grafana o cualquier recolector compatible. Los registros estructurados en JSON se escriben en la salida estándar de cada contenedor.

## Métricas principales

La métrica `nq_queue_depth` indica la cantidad de trabajos pendientes por cola y es la señal principal para decidir el escalado de workers. `nq_job_duration_seconds` es un histograma con la duración de ejecución de los trabajos; sirve para calcular percentiles p50, p95 y p99. `nq_job_failures_total` cuenta los trabajos fallidos, etiquetados por cola y por tipo de error. `nq_worker_active` informa cuántos workers están conectados y `nq_dlq_size` cuántos trabajos hay en la cola de mensajes muertos.

## Alertas recomendadas

Se recomienda alertar cuando `nq_queue_depth` crece de forma sostenida durante más de 10 minutos, lo que sugiere workers insuficientes; cuando la tasa de `nq_job_failures_total` supera el 5 % de los trabajos procesados en una ventana de 5 minutos; cuando `nq_dlq_size` es mayor que cero y no baja en 1 hora; y cuando `nq_worker_active` cae a cero con trabajos pendientes. La latencia p99 de `nq_job_duration_seconds` por encima del 80 % del `job_timeout` anticipa timeouts inminentes.

## Registros y trazabilidad

Cada trabajo recibe un identificador único `job_id` y, si el cliente lo envía, un `trace_id` que se propaga a todos los registros del trabajo. Con `nimbusctl logs --job <job_id>` se recuperan todas las líneas de log de un trabajo, incluidos los reintentos. Nimbus Queue soporta el estándar OpenTelemetry: con `NQ_OTEL_ENDPOINT` se envían trazas distribuidas a cualquier colector compatible.

## Verificación de salud

El endpoint `/healthz` verifica que el servicio esté vivo, y `/readyz` verifica además que la conexión con PostgreSQL funcione. Un balanceador debe usar `/readyz` para decidir si manda tráfico a un nodo. Un nodo que responde `/healthz` pero falla `/readyz` con el código `ERR_DB_2001` perdió la conexión con la base de datos.

## Retención de métricas

Nimbus Queue no almacena métricas históricas por sí mismo: la retención depende del sistema de monitoreo externo. Se sugiere conservar al menos 30 días de resolución de 15 segundos y 1 año de resolución agregada de 5 minutos para análisis de capacidad.

## Paneles recomendados en Grafana

Nimbus Queue publica un tablero oficial de Grafana, identificado como "Nimbus Queue Overview", que se importa desde el repositorio de despliegue. El tablero se organiza en cuatro filas. La primera muestra el estado general: cantidad de workers activos, trabajos procesados por minuto y porcentaje de fallos. La segunda muestra la profundidad de cada cola a lo largo del tiempo, con anotaciones cuando se despliega una versión nueva. La tercera presenta los percentiles de duración de los trabajos y la cuarta el estado de la cola de mensajes muertos y de las conexiones a PostgreSQL. Se recomienda fijar el rango de tiempo por defecto en las últimas 6 horas y el refresco automático en 30 segundos.

## Niveles de log y rotación

Los registros usan los niveles `DEBUG`, `INFO`, `WARNING` y `ERROR`. En producción se recomienda `INFO`; el nivel `DEBUG` genera un volumen de datos entre 10 y 20 veces mayor y puede incluir fragmentos de las cargas útiles de los trabajos, por lo que no debe activarse de forma permanente en sistemas con datos sensibles. Docker rota los logs de cada contenedor según la configuración del motor; se sugiere un tamaño máximo de 100 MB por archivo y 5 archivos de historial con el controlador `json-file`, o enviar los registros directamente a un agregador centralizado como Loki, Elasticsearch o CloudWatch.

## Objetivos de nivel de servicio sugeridos

Para operar Nimbus Queue con previsibilidad conviene fijar objetivos medibles. Un conjunto razonable de partida es: disponibilidad de la API del 99,9 % mensual, latencia p99 de encolado menor a 250 milisegundos, tiempo de espera en cola menor a 30 segundos para el 95 % de los trabajos de prioridad alta, y menos de 0,5 % de trabajos que terminen en la cola de mensajes muertos. Cada objetivo se mide con una de las métricas anteriores y se revisa en una reunión mensual. Si un objetivo se incumple de forma repetida, es una señal para ajustar la cantidad de workers o la configuración de las colas.

## Guía de respuesta ante incidentes

Ante una alerta de saturación, el procedimiento sugerido es el siguiente. Primero, confirmar si el problema afecta a todas las colas o a una sola, mirando la profundidad por cola. Segundo, revisar si hay workers activos y si sus latidos son recientes con `nimbusctl worker list`. Tercero, buscar un aumento de fallos en `nq_job_failures_total` agrupado por tipo de error, que suele revelar una dependencia externa caída. Cuarto, si la causa es capacidad, aumentar `max_workers` o la concurrencia. Quinto, una vez estabilizado el sistema, registrar el incidente con su causa raíz y las acciones preventivas. Durante todo el proceso, los identificadores `job_id` y `trace_id` permiten reconstruir qué ocurrió con los trabajos afectados.

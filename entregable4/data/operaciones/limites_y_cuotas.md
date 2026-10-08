# Límites, cuotas y rate limiting

Nimbus Queue aplica límites para proteger la estabilidad del servicio. Hay dos mecanismos distintos: el *rate limiting*, que acota la cantidad de solicitudes por unidad de tiempo, y las *cuotas*, que acotan la cantidad total de recursos que una cuenta puede almacenar.

## Rate limiting de la API

Cada token de API tiene un límite de 600 solicitudes por minuto en el plan Estándar y de 6000 solicitudes por minuto en el plan Enterprise. Se usa un algoritmo de cubo de fichas (*token bucket*) que permite ráfagas cortas de hasta el doble del límite sostenido. Cuando se supera el límite, la API responde con HTTP 429 y el código `ERR_RATE_5001`. La respuesta incluye el encabezado `Retry-After` con los segundos que hay que esperar.

Los clientes deben respetar `Retry-After` y aplicar espera exponencial con variación aleatoria. Reintentar de inmediato agrava el problema y puede llevar a un bloqueo temporal del token.

## Cuotas de cuenta

Las cuotas por defecto del plan Estándar son: 50 colas, 100 mil trabajos pendientes por cola, tamaño máximo de carga útil de 256 KB por trabajo y retención de resultados de 7 días. El plan Enterprise sube esos valores a 500 colas, 5 millones de trabajos pendientes por cola, 1 MB de carga útil y 30 días de retención.

Al intentar encolar un trabajo con una carga útil mayor a la permitida, la API responde HTTP 413 con el código `ERR_QUOTA_6002`. Al superar la cantidad máxima de trabajos pendientes de una cola, responde HTTP 429 con `ERR_QUOTA_6001`; la diferencia con el rate limiting es que este último se resuelve esperando segundos, mientras que una cuota solo se libera cuando se completan o descartan trabajos.

## Consulta de uso

El uso actual se consulta con `GET /v1/account/usage` o con `nimbusctl quota show`. La respuesta detalla, por cada cuota, el valor consumido, el límite y el porcentaje. Se puede configurar una alerta que notifique al llegar al 80 % y al 95 % de cualquier cuota.

## Solicitud de aumento de cuotas

Los aumentos se piden desde el panel de administración, en "Facturación y límites", o escribiendo al equipo de soporte. Los aumentos moderados, de hasta el doble del valor actual, se aprueban automáticamente en menos de una hora. Los superiores requieren revisión manual y demoran hasta 2 días hábiles.

## Encabezados informativos de límites

Todas las respuestas de la API incluyen encabezados que permiten al cliente conocer su situación sin esperar a recibir un error. `X-RateLimit-Limit` informa el máximo de solicitudes permitidas en la ventana actual, `X-RateLimit-Remaining` cuántas quedan y `X-RateLimit-Reset` el instante, en segundos desde el epoch, en que la ventana se renueva. Un cliente bien diseñado consulta `X-RateLimit-Remaining` y reduce su ritmo de forma preventiva cuando el valor se acerca a cero, en lugar de depender de recibir una respuesta de error para frenar.

## Límites por endpoint

Algunos endpoints tienen límites propios, más estrictos que el general, por su costo de procesamiento. El endpoint de encolado en lote, `POST /v1/jobs/batch`, acepta hasta 1000 trabajos por llamada y cuenta como una sola solicitud a efectos del límite por minuto, lo que lo hace mucho más eficiente que encolar trabajos de a uno. La búsqueda de trabajos con filtros, `GET /v1/jobs/search`, está limitada a 60 llamadas por minuto, y la exportación de resultados, `POST /v1/exports`, a 10 por hora. Estos límites específicos se informan en la documentación de cada endpoint.

## Almacenamiento y retención

Además de las cuotas de cantidad, existe una cuota de almacenamiento total para las cargas útiles y los resultados: 10 GB en el plan Estándar y 500 GB en el plan Enterprise. Cuando se supera el 90 % de esta cuota, Nimbus Queue comienza a descartar primero los resultados más antiguos que ya superaron su período de retención. Si aun así el almacenamiento sigue lleno, la API empieza a rechazar nuevos encolados con el código `ERR_QUOTA_6003`. Para evitarlo, se recomienda guardar las cargas útiles grandes en un almacenamiento externo, como un bucket de objetos, y enviar en el trabajo solo una referencia.

## Entornos de prueba

Las cuentas pueden crear entornos de prueba aislados, con sus propias colas y tokens, que tienen cuotas reducidas: 5 colas, 1000 trabajos pendientes por cola y retención de 24 horas. Estos entornos no consumen la cuota del entorno de producción y se eliminan automáticamente después de 30 días sin actividad. Son adecuados para pruebas de integración y para ensayar cambios de configuración antes de aplicarlos en producción.

## Planes y facturación

La facturación se basa en la cantidad de trabajos procesados por mes y no en el tiempo de ejecución. El plan Estándar incluye 1 millón de trabajos mensuales; los trabajos adicionales se cobran por tramos de 100 mil. El plan Enterprise ofrece un volumen negociado, soporte con respuesta en menos de 1 hora y un acuerdo de nivel de servicio del 99,95 % de disponibilidad. El detalle del consumo se descarga como archivo CSV desde la sección "Facturación y límites" del panel.

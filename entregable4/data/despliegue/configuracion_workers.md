# Configuración y escalado de workers

Los workers son los procesos que ejecutan los trabajos encolados. Cada worker se suscribe a una o más colas y toma trabajos según su prioridad. La configuración se define en el archivo `worker.yaml` o con variables de entorno que empiezan con `NQ_WORKER_`.

## Concurrencia

El parámetro `concurrency` (variable `NQ_WORKER_CONCURRENCY`) define cuántos trabajos ejecuta en paralelo un mismo worker. El valor por defecto es 4. Para trabajos que consumen mucha CPU conviene fijarlo igual a la cantidad de núcleos; para trabajos que esperan entradas y salidas, como llamadas HTTP, se puede subir hasta 10 veces el número de núcleos. Un valor demasiado alto genera contención de memoria y activa el error `ERR_WRK_3005` (worker sin memoria disponible).

## Timeouts y reintentos

Cada trabajo tiene un tiempo máximo de ejecución, `job_timeout`, de 300 segundos por defecto. Si el trabajo lo excede, el worker lo cancela y lo marca como `timeout`. El parámetro `max_retries` (3 por defecto) define cuántas veces se reintenta un trabajo fallido. Entre reintentos se aplica una espera exponencial: 10 segundos, 40 segundos, 160 segundos, con un factor de variación aleatoria del 20 % para evitar que todos los reintentos coincidan.

Cuando se agotan los reintentos, el trabajo pasa a la cola de mensajes muertos (*dead letter queue*), llamada `dlq` por defecto. Los trabajos en la `dlq` se conservan 14 días y se pueden volver a encolar con `nimbusctl job requeue --from-dlq`.

## Autoescalado

Nimbus Queue puede escalar workers automáticamente según la profundidad de la cola. La regla por defecto agrega un worker cada 500 trabajos pendientes y quita uno cuando la cola lleva más de 5 minutos con menos de 50 trabajos. Los límites `min_workers` y `max_workers` acotan el rango; con `min_workers: 0` el sistema puede apagar todos los workers cuando no hay trabajo, a costa de un arranque en frío de 20 a 40 segundos.

## Apagado ordenado

Al recibir la señal SIGTERM, el worker deja de tomar trabajos nuevos y espera hasta `shutdown_grace` (30 segundos por defecto) a que terminen los que están en curso. Los trabajos que no terminan en ese plazo se devuelven a la cola para que otro worker los retome. Se recomienda que los trabajos sean idempotentes, porque un trabajo interrumpido puede ejecutarse más de una vez.

## Etiquetas y enrutamiento

Los workers pueden declarar etiquetas, por ejemplo `gpu` o `region-sa`. Un trabajo encolado con `required_tags` solo lo tomará un worker que tenga todas esas etiquetas. Si ningún worker cumple, el trabajo queda en estado `pending` hasta que aparezca uno.

## Prioridades entre colas

Un worker suscripto a varias colas necesita una regla para decidir de cuál tomar el siguiente trabajo. Con `strategy: strict` consume siempre primero la cola de mayor prioridad y solo pasa a la siguiente cuando la anterior está vacía; es la opción adecuada cuando hay una cola crítica, como pagos, que nunca debe esperar. Con `strategy: weighted` reparte el consumo en proporción a un peso asignado a cada cola, por ejemplo 5 a 3 a 1, lo que evita que las colas de baja prioridad queden sin atender indefinidamente. La estrategia por defecto es `weighted` con pesos iguales.

## Trabajos programados

Además de los trabajos encolados por la API, Nimbus Queue permite definir trabajos periódicos con expresiones cron en el archivo `schedules.yaml`. Por ejemplo, `"0 3 * * *"` ejecuta un trabajo todos los días a las 3 de la madrugada, en la zona horaria definida por `NQ_TIMEZONE`, que por defecto es UTC. El planificador garantiza que, aunque haya varias instancias de `nq-scheduler` corriendo por alta disponibilidad, cada ocurrencia se encole una sola vez, gracias a un bloqueo distribuido en la base de datos. Si el scheduler estuvo caído durante una ejecución programada, la política `catch_up` decide si se recupera la ejecución perdida o se descarta.

## Claves de deduplicación

Para evitar que el mismo trabajo se encole dos veces, por ejemplo ante un reintento del cliente HTTP, se puede enviar el encabezado `Idempotency-Key` con un valor único elegido por el cliente. Si Nimbus Queue recibe otra solicitud con la misma clave dentro de las 24 horas siguientes, devuelve el trabajo original en lugar de crear uno nuevo. Las claves de deduplicación se almacenan por cola y caducan automáticamente pasadas esas 24 horas.

## Gestión de memoria y reciclado de procesos

Los trabajos de larga duración pueden acumular memoria con el tiempo. El parámetro `max_jobs_per_process` hace que cada proceso hijo del worker se recicle después de ejecutar esa cantidad de trabajos, liberando la memoria acumulada; su valor por defecto es 1000. De forma complementaria, `max_memory_mb` define un tope por proceso: si se supera, el proceso termina de forma ordenada al finalizar el trabajo actual y se reemplaza por uno nuevo. Estos mecanismos son preferibles a aumentar indefinidamente los recursos del contenedor.

## Registro de workers

Cada worker se registra al arrancar en el scheduler con su identificador, sus etiquetas y su capacidad. Mientras está activo, envía una señal de latido (*heartbeat*) cada 10 segundos. Si el scheduler no recibe latidos durante 60 segundos, considera al worker caído y devuelve a la cola los trabajos que tenía asignados. El estado de todos los workers puede consultarse con `nimbusctl worker list`, que muestra su versión, la cantidad de trabajos en curso y el momento del último latido.

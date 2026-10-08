# Instalación de Nimbus Queue con Docker

La forma recomendada de desplegar Nimbus Queue en desarrollo y en entornos pequeños es Docker Compose. Se necesita Docker 24 o superior, Docker Compose v2 y, como mínimo, 2 CPU y 4 GB de memoria disponibles para el stack completo.

## Servicios del stack

El archivo `docker-compose.yml` oficial define cuatro servicios: `nq-api`, que expone la API REST en el puerto 8080; `nq-scheduler`, que decide qué trabajo se asigna a qué worker; `nq-worker`, que ejecuta los trabajos; y `nq-postgres`, la base de datos PostgreSQL 15 donde se persiste el estado de colas y trabajos. Redis es opcional y se usa solo para cachear el estado de las colas cuando hay más de 10 mil trabajos por minuto.

## Pasos de instalación

Primero se clona el repositorio de despliegue y se copia la plantilla de configuración con `cp .env.example .env`. En ese archivo se deben cambiar obligatoriamente `NQ_SECRET_KEY`, que firma las sesiones, y `POSTGRES_PASSWORD`. Luego se levanta el stack con `docker compose up -d` y se verifica la salud con `curl http://localhost:8080/healthz`, que debe devolver `{"status": "ok"}`.

La primera vez, la base de datos se inicializa automáticamente con las migraciones. Si se actualiza a una versión nueva, hay que correr las migraciones a mano con `docker compose run --rm nq-api nimbusctl db migrate` antes de reiniciar los servicios. No ejecutar este paso es la causa más común del error `ERR_DB_2010` (esquema desactualizado) durante el arranque.

## Volúmenes y persistencia

Los datos de PostgreSQL viven en el volumen nombrado `nq_pgdata`. Hacer `docker compose down -v` borra ese volumen y, con él, todos los trabajos y colas. Para respaldar la base se usa `docker compose exec nq-postgres pg_dump -U nimbus nimbus > respaldo.sql`.

## Puertos y red

Por defecto solo `nq-api` publica un puerto hacia el host (8080). El resto de los servicios se comunican por la red interna `nq_net`. En producción se recomienda colocar un proxy inverso con TLS delante de `nq-api` y no exponer nunca el puerto 5432 de PostgreSQL a internet.

## Actualización

Antes de actualizar se debe hacer un respaldo, descargar las imágenes nuevas con `docker compose pull`, correr las migraciones y recién después ejecutar `docker compose up -d`. Nimbus Queue garantiza compatibilidad de la API dentro de la misma versión mayor; los saltos de versión mayor requieren leer las notas de migración.

## Variables de entorno principales

La configuración del stack se controla con variables de entorno definidas en el archivo `.env`. `NQ_SECRET_KEY` es la clave que firma las sesiones del panel de administración y debe tener al menos 32 caracteres aleatorios. `NQ_DATABASE_URL` indica la cadena de conexión a PostgreSQL; por defecto apunta al servicio interno `nq-postgres`, pero puede cambiarse para usar una base gestionada, como Amazon RDS. `NQ_LOG_LEVEL` acepta `debug`, `info`, `warning` y `error`, y su valor por defecto es `info`. `NQ_PUBLIC_URL` define la URL externa con la que se generan los enlaces del panel y los webhooks. `NQ_REDIS_URL` activa el uso de Redis como caché cuando se define.

Una configuración mal escrita se detecta al arranque: `nq-api` valida todas las variables antes de abrir el puerto y, si alguna es inválida, termina con un mensaje que indica cuál. El comando `docker compose config` permite revisar cómo quedaron resueltas las variables antes de levantar los servicios.

## Recursos y límites de los contenedores

En un entorno de producción pequeño se sugiere asignar 1 CPU y 1 GB de memoria a `nq-api`, 1 CPU y 512 MB a `nq-scheduler`, y entre 2 y 4 GB a `nq-postgres`. Los límites se declaran en `docker-compose.yml` con la sección `deploy.resources.limits`. Un contenedor que supera su límite de memoria es terminado por el sistema operativo; en los logs de Docker aparece como `OOMKilled`, y el servicio se reinicia solo si se configuró `restart: unless-stopped`.

## Instalación en Kubernetes con Helm

Para entornos más grandes se ofrece un chart de Helm oficial. Se instala agregando el repositorio con `helm repo add nimbusqueue https://charts.nimbusqueue.example` y luego `helm install nimbus nimbusqueue/nimbus-queue -f valores.yaml`. El archivo de valores permite definir la cantidad de réplicas de `nq-api` (mínimo 2 para alta disponibilidad), el tamaño del volumen persistente y la clase de almacenamiento. El chart crea además un recurso de entrada (`Ingress`) con TLS y un `PodDisruptionBudget` que evita que una actualización del clúster deje la API sin réplicas disponibles.

## Solución de problemas del arranque

Si `nq-api` se reinicia en bucle, el primer paso es mirar sus logs con `docker compose logs nq-api`. Las causas más comunes son una variable de entorno inválida, la base de datos todavía no disponible o el puerto 8080 ocupado por otro proceso del host. Si `nq-postgres` tarda en estar listo, el servicio `nq-api` espera hasta 60 segundos antes de abandonar; ese plazo se ajusta con `NQ_DB_WAIT_SECONDS`. Para comprobar la conectividad con la base se puede ejecutar `docker compose exec nq-api nimbusctl db ping`, que responde `ok` cuando la conexión funciona.

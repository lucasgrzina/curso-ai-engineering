# Autenticación con tokens de API en Nimbus Queue

Nimbus Queue es una plataforma de colas de trabajos distribuidos. Toda llamada a su API REST debe estar autenticada con un token de API enviado en el encabezado `Authorization: Bearer <token>`. Los tokens se generan desde el panel de administración, en la sección "Credenciales", o con el comando `nimbusctl token create`.

## Tipos de token

Existen tres tipos de token. El token de **lectura** (prefijo `nq_ro_`) solo permite consultar el estado de colas y trabajos. El token de **escritura** (prefijo `nq_rw_`) permite además encolar, cancelar y reintentar trabajos. El token de **administración** (prefijo `nq_adm_`) permite crear colas, modificar cuotas y gestionar otros tokens. Se recomienda usar siempre el token con el mínimo privilegio necesario para cada servicio.

## Vencimiento y rotación

Por defecto, un token vence a los 90 días de su creación. El valor puede ajustarse entre 1 y 365 días con el parámetro `--ttl-days`. Nimbus Queue envía una notificación por correo electrónico 14 días antes del vencimiento. Para rotar un token sin cortar el servicio, se crea el token nuevo, se actualiza la aplicación cliente y recién entonces se revoca el anterior con `nimbusctl token revoke <id>`. Durante la ventana de rotación ambos tokens son válidos al mismo tiempo.

Un token revocado deja de funcionar de inmediato en todos los nodos del clúster, aunque la propagación completa puede tardar hasta 30 segundos en regiones remotas.

## Buenas prácticas de seguridad

Nunca se debe guardar un token en el repositorio de código ni en variables de entorno de imágenes publicadas. Debe inyectarse en tiempo de ejecución desde un gestor de secretos. Si se sospecha que un token fue expuesto, hay que revocarlo de inmediato y revisar el registro de auditoría, que conserva 180 días de historial de uso por token, incluyendo dirección IP de origen y endpoint consultado.

Cuando un token es inválido, la API responde con el código HTTP 401 y el cuerpo `{"error": "ERR_AUTH_1003", "detalle": "token inexistente, vencido o revocado"}`. Si el token es válido pero no tiene permisos suficientes para la operación, responde con HTTP 403 y el código `ERR_AUTH_1007`. Un aumento repentino de respuestas `ERR_AUTH_1003` suele indicar un token vencido que quedó configurado en algún servicio olvidado.

El límite de intentos fallidos de autenticación es de 20 por minuto por dirección IP. Superado ese límite, la IP queda bloqueada durante 15 minutos y la API responde con el código `ERR_AUTH_1012`.

## Uso del token desde distintos clientes

Con `curl`, el token se envía en el encabezado de la solicitud: `curl -H "Authorization: Bearer $NQ_TOKEN" https://api.nimbusqueue.example/v1/queues`. En Python, con la librería `requests`, se arma un objeto de sesión que fija el encabezado una sola vez: `sesion = requests.Session()` y luego `sesion.headers["Authorization"] = f"Bearer {token}"`, de modo que todas las llamadas posteriores lo incluyan sin repetirlo. El SDK oficial de Nimbus Queue para Python, `nimbusqueue`, lee el token de la variable de entorno `NQ_TOKEN` si no se le pasa explícitamente, y lo mismo hacen los SDK de Node.js y de Go.

Nunca se debe enviar el token como parámetro de la URL, por ejemplo `?token=...`. Las URL quedan registradas en los logs de proxies, balanceadores y navegadores, por lo que un token en la URL queda expuesto. La API rechaza ese formato con el código `ERR_AUTH_1015`.

## Tokens de servicio y tokens personales

Los **tokens personales** están asociados a una persona del equipo y heredan sus permisos; se invalidan automáticamente cuando esa persona es dada de baja de la organización. Los **tokens de servicio** pertenecen a la organización y se usan para aplicaciones, tareas programadas y pipelines de integración continua. Se recomienda que ninguna aplicación de producción use un token personal, porque su funcionamiento quedaría atado a la permanencia de un empleado en el equipo.

Cada token puede llevar una lista de **direcciones IP permitidas** en formato CIDR, por ejemplo `203.0.113.0/24`. Una solicitud con un token válido desde una IP fuera de la lista se rechaza con HTTP 403. Esta restricción es especialmente útil para tokens de administración, que deberían usarse solo desde la red corporativa o desde un bastión.

## Alertas y auditoría de uso

El registro de auditoría puede exportarse a un sistema externo mediante un webhook o un bucket de almacenamiento compatible con S3. Cada evento incluye el identificador del token, el tipo de operación, el recurso afectado, la dirección IP y la marca de tiempo en UTC. Se pueden definir alertas sobre patrones sospechosos: uso del mismo token desde más de tres países en una hora, creación de tokens de administración fuera del horario laboral, o un aumento de más del 300 % en el volumen de solicitudes de un token respecto a su promedio semanal.

## Preguntas frecuentes

¿Se puede recuperar un token perdido? No: el valor completo del token se muestra una única vez al crearlo y Nimbus Queue solo conserva un hash. Si se pierde, hay que crear uno nuevo y revocar el anterior. ¿Hay un límite de tokens por cuenta? Sí, 200 tokens activos en el plan Estándar y 2000 en el plan Enterprise. ¿Los tokens revocados siguen contando para ese límite? No, dejan de contar apenas se revocan, pero permanecen visibles en la auditoría durante 180 días.

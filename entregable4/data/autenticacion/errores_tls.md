# Conexiones seguras y errores TLS en Nimbus Queue

Nimbus Queue exige cifrado de transporte en todas las conexiones. El endpoint público acepta únicamente TLS 1.2 y TLS 1.3; los protocolos anteriores (SSL 3.0, TLS 1.0 y TLS 1.1) están deshabilitados desde la versión 4.0. El certificado del servidor está firmado por una autoridad certificante pública, por lo que los clientes estándar no necesitan configuración adicional.

## Certificados de cliente (mTLS)

Para entornos de alta seguridad se puede activar la autenticación mutua (mTLS). En ese modo, además del token de API, el cliente debe presentar un certificado X.509 firmado por la autoridad certificante interna de la organización. El certificado de la CA se carga en el clúster con `nimbusctl tls set-ca ca.pem`. La verificación se hace en el balanceador de entrada, antes de llegar al servicio de autenticación.

## Catálogo de errores TLS

El error `ERR_TLS_4021` indica que el certificado del servidor presentado por el nodo está vencido o todavía no es válido. Casi siempre se debe a un certificado que no se renovó a tiempo o a un reloj del servidor desfasado. La solución es renovar el certificado con `nimbusctl tls renew` y verificar la sincronización horaria con NTP.

El error `ERR_TLS_4022` significa que la cadena de certificados está incompleta: falta el certificado intermedio. El servidor debe enviar la cadena completa (hoja más intermedios), no solo el certificado final.

El error `ERR_TLS_4030` aparece cuando el cliente intenta negociar una versión de protocolo no soportada, por ejemplo TLS 1.0 desde una librería antigua. Hay que actualizar la librería del cliente o la versión de Python o Java que la contiene.

El error `ERR_TLS_4045` se produce en modo mTLS cuando el certificado del cliente fue revocado o no fue emitido por la CA registrada en el clúster.

## Diagnóstico rápido

Para inspeccionar el certificado que presenta un nodo se puede usar `openssl s_client -connect api.nimbusqueue.example:443 -showcerts`. El comando `nimbusctl tls check` realiza una verificación completa: fecha de vencimiento, cadena, versión de protocolo y suites de cifrado aceptadas. Se recomienda programarlo cada 24 horas y alertar cuando falten menos de 21 días para el vencimiento de cualquier certificado.

Los errores TLS se registran en el log del balanceador con el nivel `ERROR` y el prefijo `tls_handshake`. Cada ocurrencia incluye la IP del cliente, la versión de protocolo ofrecida y el código de error.

## Configuración de la validación de certificados en los clientes

Los clientes deben validar siempre el certificado del servidor. En Python, la librería `requests` usa por defecto el paquete de autoridades certificantes `certifi`; para una CA interna se indica la ruta del archivo con el parámetro `verify="/etc/ssl/ca-interna.pem"`. Desactivar la validación con `verify=False` elimina la protección contra ataques de intermediario y no debe usarse fuera de pruebas locales. En Java, las CA internas se agregan al almacén de confianza con la herramienta `keytool -importcert`. En Node.js se define la variable de entorno `NODE_EXTRA_CA_CERTS` apuntando al archivo PEM.

Los SDK oficiales de Nimbus Queue aceptan la variable `NQ_CA_BUNDLE` para indicar un paquete de CA propio sin modificar el código de la aplicación. También respetan las variables estándar `SSL_CERT_FILE` y `SSL_CERT_DIR`.

## Renovación automática de certificados

Para los endpoints propios del clúster se recomienda automatizar la renovación con el protocolo ACME, por ejemplo con `certbot` o con `cert-manager` en Kubernetes. Nimbus Queue puede recargar un certificado nuevo en caliente, sin reiniciar el balanceador, enviando la señal SIGHUP o ejecutando `nimbusctl tls reload`. Una renovación exitosa genera el evento `tls_certificate_renewed` en el registro de auditoría, con la huella SHA-256 del certificado anterior y la del nuevo.

Los certificados emitidos por ACME suelen durar 90 días y conviene renovarlos cuando falten 30. Programar la renovación con tan poco margen como 3 o 4 días deja al servicio expuesto si el primer intento falla por un problema transitorio de red o de cuota con la autoridad emisora.

## Rotación de la CA interna en modo mTLS

Cuando se rota la autoridad certificante interna, el clúster debe confiar en la CA vieja y en la nueva al mismo tiempo durante una ventana de transición. El procedimiento es: cargar la CA nueva junto a la anterior con `nimbusctl tls add-ca nueva.pem`, emitir los certificados de cliente con la CA nueva, esperar a que todos los clientes los hayan actualizado y recién entonces quitar la CA vieja con `nimbusctl tls remove-ca`. Quitar la CA anterior antes de tiempo deja afuera a los clientes que todavía no migraron.

## Proxies corporativos que interceptan TLS

En muchas redes corporativas, un proxy de inspección termina la conexión TLS y presenta su propio certificado, firmado por una CA de la empresa. Si esa CA no está instalada en el cliente, la conexión falla aunque el servidor de Nimbus Queue esté sano. El síntoma típico es que el mismo código funciona fuera de la red corporativa y falla dentro de ella. La solución correcta es instalar la CA del proxy en el almacén de confianza del cliente o excluir el dominio de Nimbus Queue de la inspección; nunca desactivar la validación del certificado.

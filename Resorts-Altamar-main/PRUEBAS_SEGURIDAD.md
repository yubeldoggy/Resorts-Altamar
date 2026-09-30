# Pruebas de seguridad – Resorts Altamar

Prototipo local (`app.py`, Python 3.11, SQLite). Pruebas hechas el 30-09-2026 sobre el servidor en funcionamiento (`http://127.0.0.1:8080`) y con pruebas automáticas (`python -m unittest -v`).

## 1. Qué se probó

Se enviaron **1.006 peticiones** con datos erróneos o maliciosos a los 12 endpoints que reciben datos, y se revisó la respuesta de cada una.

| Tipo de prueba | Ejemplos de datos enviados |
|---|---|
| Tipos y valores extraños en cada campo | `null`, `true`, `-1`, `3.7`, infinito, números de 400 dígitos, listas, objetos, `{"$gt": ""}` |
| Inyección SQL | `1 OR 1=1`, `1'; DROP TABLE users;--`, cookie `' OR '1'='1` |
| XSS (inyección de HTML/JavaScript) | `<script>alert(1)</script>`, `"><img src=x onerror=alert(1)>` |
| Caracteres especiales | byte nulo, texto de derecha a izquierda (`U+202E`), sustitutos Unicode inválidos, dígitos árabes (`١٢٣`), superíndices (`²`) |
| Cuerpos HTTP malformados | JSON cortado, bytes inválidos, 3.000 niveles de anidación, `Content-Length` falso o negativo |
| CSRF / origen | `Origin` de otro sitio, `Origin: null`, `Host` ajeno (DNS rebinding), formularios `x-www-form-urlencoded` |
| Control de acceso (IDOR y roles) | cliente sobre reservas ajenas, recepción sobre otro hotel, cliente/recepción en bitácora o servicios, sin sesión, cookie inventada, registro pidiendo rol gerente |
| Rutas y métodos | `/../app.py`, `/%2e%2e/app.py`, `/altamar.sqlite3`, `/estacion.json`; métodos PUT, DELETE, PATCH, OPTIONS, HEAD, TRACE |
| Reglas de negocio | fechas pasadas, salida antes de la llegada, 61 noches, año 9999, cantidades 0/21/negativas, precios negativos o decimales |
| Enumeración y fuerza bruta | mismo mensaje y tiempo para RUT existente e inexistente; registro de 30 cuentas seguidas |

Además, se insertaron textos maliciosos **directamente en la base de datos** (nombre de huésped, de servicio, de hotel, de usuario y bitácora) y se abrió la aplicación en un navegador como cliente y como gerente, para comprobar que no se ejecutaran.

## 2. Resultados

| Indicador | Antes de corregir | Después |
|---|---|---|
| Respuestas con error 500 | 32 | **0** |
| Respuestas que exponían mensajes internos de Python | 86 | **0** |
| Inyección SQL exitosa | 0 (las 5 tablas intactas) | 0 |
| XSS ejecutado en el navegador | 0 | 0 |
| Accesos indebidos entre usuarios, roles u hoteles | 0 | 0 |
| Peticiones de otro sitio aceptadas (CSRF) | 0 | 0 |
| Archivos internos descargables (`app.py`, base de datos) | 0 | 0 |
| Diferencia de tiempo en login: RUT existente vs. inexistente | ~0 ms (134–178 ms ambos) | — |

## 3. Hallazgos y correcciones

| # | Hallazgo | Severidad | Corrección |
|---|---|---|---|
| 1 | IDs o números infinitos o de 40 dígitos provocaban error 500 (32 casos) | Media | Función `to_int`: solo enteros con dígitos 0-9 y dentro de un rango |
| 2 | Mensajes internos de Python en las respuestas (p. ej. `invalid literal for int()`, errores del lector JSON) | Baja | Separación entre errores del usuario (`InputError`, mensaje claro) y errores internos (mensaje genérico) |
| 3 | JSON con 3.000 niveles de anidación provocaba error 500 | Media | Se responde 400 genérico |
| 4 | Métodos PUT, DELETE, etc. devolvían la página de error por defecto, sin cabeceras de seguridad | Baja | Responden 405 con las mismas cabeceras que el resto |
| 5 | Se aceptaban reservas para el año 9999 | Baja | Máximo 2 años de anticipación |
| 6 | Precios con dígitos árabes (`٥٠`) se aceptaban como 50 | Baja | Solo dígitos 0-9 |
| 7 | Se podían crear cuentas sin límite (30 en 5 segundos) | Baja | Máximo 10 intentos de registro por equipo cada 10 minutos |

Cada corrección tiene su prueba automática en `test_app.py` (clases `InputHardeningTests` y `HttpSecurityTests`). Total: 47 pruebas, todas aprobadas.

## 4. Riesgos que quedan (aceptados en el prototipo)

- **Sin HTTPS:** la conexión es `http://` local; al publicarse se necesitaría un certificado.
- **Enumeración por registro:** el registro público avisa si un RUT ya tiene cuenta. El límite de intentos lo frena; eliminarlo requiere verificar la identidad (por ejemplo, por correo).
- **Bloqueo como denegación de servicio:** 5 intentos fallidos bloquean un RUT 5 minutos, también si los hace otra persona.
- **Cuentas de prueba públicas** en el README: solo para la demostración.

## 5. Relación con ISO/IEC 27001:2022 (Anexo A)

| Control | Cómo se aplica |
|---|---|
| 5.15 Control de acceso | Permisos por rol y por hotel validados en el servidor |
| 5.17 Información de autenticación | Contraseñas con hash PBKDF2-SHA256, sal y política de claves |
| 8.5 Autenticación segura | Bloqueo por intentos, mensaje único de error, sesión aleatoria con vencimiento |
| 8.15 Registro de eventos | Bitácora de accesos, fallos, bloqueos, reservas, cobros y servicios |
| 8.28 Codificación segura | Consultas SQL con parámetros, escape de HTML, validación de entradas, errores sin detalles internos |
| 8.29 Pruebas de seguridad | Este informe y las pruebas automáticas |

*Los números de control se tomaron de fuentes secundarias sobre la norma; conviene contrastarlos con el texto oficial.*

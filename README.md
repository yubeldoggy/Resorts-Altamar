# Resorts Altamar - prototipo de reservas

Aplicación local para crear, listar y cancelar reservas, con inicio de sesión por RUT, registro de clientes y tres roles. Incluye 20 hoteles en 4 regiones, con 5 habitaciones de demostración por hotel. Los datos se guardan en `altamar.sqlite3`, creado al iniciar.

## Abrir

En Windows, haz doble clic en **INICIAR.bat**. Necesita Python 3.11 o posterior; no necesita instalar paquetes. El iniciador también reconoce el Python incluido con Codex en este equipo.

O ejecuta desde esta carpeta:

```sh
python app.py --open
```

Abre http://127.0.0.1:8080. Mantén la consola abierta mientras lo usas. Para detenerlo, presiona Ctrl+C en la consola. No abras `index.html` directamente: necesita el servidor.

## Cuentas de prueba

| Rol | Nombre | RUT | Contraseña |
|---|---|---|---|
| Cliente | Cliente de prueba | 11.111.111-1 | Cliente#2026 |
| Recepción | Damián Sandoval | 22.013.635-3 | gNvFAxfNppb4 |
| Gerente | Eduardo Salinas | 16.091.233-2 | w3TJXFFSRXCT |

Los RUT son ficticios y tienen dígito verificador válido. Las cuentas se crean automáticamente la primera vez que se inicia la aplicación. Si ya existe un archivo `altamar.sqlite3` de una versión anterior, bórralo para que se creen estas cuentas (se pierden las reservas de prueba guardadas).

## Crear clientes nuevos

- **El huésped se registra solo:** en la pantalla de acceso, pestaña **Crear cuenta** (RUT, nombre y contraseña). Queda con sesión iniciada. Siempre se crea con rol cliente: no es posible elegir otro rol.
- **Recepción lo registra:** en su panel, sección **Registrar cliente**, con una contraseña temporal. Después puede elegirlo en **Nueva reserva** y la reserva aparece en la cuenta del cliente.

Las cuentas de recepción y gerente no se crean desde la aplicación.

## Acceso y roles

- **Cliente:** crea reservas a su nombre, solo ve y cancela las suyas, y puede modificar las fechas de sus reservas confirmadas.
- **Recepción:** registra clientes, crea reservas para clientes con cuenta o huéspedes sin cuenta, administra todas las reservas y registra check-in/check-out.
- **Gerente:** ve y cancela todas las reservas y revisa la **actividad de seguridad**. No crea reservas.

Los permisos se validan en el servidor, no solo en la pantalla.

## Servicios y comprobantes

- Al crear una reserva, el huésped o recepción puede agregar spa ($35.000), tour ($50.000) y servicio a la habitación ($18.000). Se admiten hasta 10 unidades de cada servicio.
- Las reservas muestran sus noches, el costo del alojamiento, el subtotal de servicios y el total en pesos chilenos. La tarifa demostrativa es de $80.000 por noche.
- En **Servicios** se pueden agregar, cambiar o quitar servicios de una reserva confirmada o en curso. Los cambios actualizan los subtotales.
- **Comprobante** muestra fechas, huésped, alojamiento, servicios, cantidades y total, y se puede imprimir. Es solo informativo: no procesa pagos ni emite una factura.
- Si no hay cupo en un hotel, la aplicación sugiere hoteles disponibles de la misma región para las mismas fechas.
- Recepción puede cambiar una reserva de **Confirmada** a **En curso** con check-in y de **En curso** a **Finalizada** con check-out. Las transiciones quedan registradas en la bitácora.
- La lista permite buscar por huésped, hotel, código o fechas y filtrar por estado y rango de fechas.

## Seguridad aplicada

- **RUT:** se valida el formato y el dígito verificador (módulo 11), en pantalla y en el servidor. Se acepta con o sin puntos y guion.
- **Contraseñas:** se guardan con hash PBKDF2-SHA256, sal aleatoria por usuario y 600.000 iteraciones; nunca en texto plano. La comparación usa tiempo constante (`hmac.compare_digest`).
- **Política de contraseñas** (registro y clientes creados por recepción): entre 8 y 64 caracteres, con letras y números. Se rechazan las claves de una lista corta de claves comunes y las que contienen el RUT.
- **Bloqueo por intentos:** tras 5 intentos fallidos, ese RUT queda bloqueado 5 minutos, incluso con la clave correcta. También se aplica a RUT que no existen.
- **Sin pistas para atacantes:** si el RUT no existe o la clave es incorrecta, el mensaje es el mismo y la respuesta tarda aproximadamente lo mismo.
- **Sesión:** token aleatorio en una cookie `HttpOnly` y `SameSite=Strict`, que vence a las 8 horas. En la base solo se guarda el hash del token. Cerrar sesión lo invalida.
- **Solicitudes de otros sitios:** el servidor rechaza operaciones que no vengan de la propia aplicación (revisión de `Host` y `Origin`).
- **Validación de datos:** nombres solo con letras, espacios, apóstrofo, guion o punto. Todo lo que se muestra en pantalla se escapa para evitar inyección de HTML (XSS). Las consultas SQL usan parámetros.
- **Cabeceras de seguridad:** `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` y `Cross-Origin-Opener-Policy`. El servidor no revela la versión de Python.
- **Errores:** los errores inesperados muestran un mensaje genérico, sin detalles internos.
- **Bitácora:** se registran inicios y cierres de sesión, accesos fallidos, bloqueos, registros de clientes y cambios de reservas (fechas, servicios, cancelaciones y check-in/check-out). Solo gerencia puede verla.

**Límites conocidos:** la aplicación funciona con `http://` local, así que el tráfico entre el navegador y el servidor no va cifrado; para publicarla haría falta HTTPS. Tampoco incluye recuperación de contraseña ni facturación o procesamiento de pagos. El bloqueo por intentos podría usarse para bloquear a propósito la cuenta de otra persona durante 5 minutos.

## Probar

1. Inicia sesión como Recepción.
2. En **Registrar cliente**, crea un cliente con un RUT válido (por ejemplo 12.345.678-5).
3. Crea una reserva para ese cliente y otra para un huésped sin cuenta.
4. Reserva las mismas fechas cinco veces en el mismo hotel. La sexta solicitud se rechaza.
5. Cancela una reserva: aparece una confirmación y el cupo queda disponible de nuevo.
6. Cierra sesión y entra con el cliente creado: solo verá su reserva.
7. Cierra sesión e intenta entrar 5 veces con una clave incorrecta: el RUT queda bloqueado 5 minutos.
8. Entra como Gerente y revisa la **Actividad de seguridad**.

La salida no ocupa una noche: una persona puede salir el día en que otra llega. La estadía admite entre 1 y 60 noches. La base de datos bloquea las escrituras concurrentes antes de consultar cupos y una clave única por hotel, habitación y noche impide duplicarlos.

## Archivos

- `app.py`: servidor, base de datos, acceso (RUT, contraseñas, sesiones, bloqueo y roles), registro de clientes, bitácora y reglas de reservas.
- `static/index.html`: pantalla de acceso y registro, panel de reservas y bitácora.
- `static/style.css`: apariencia y adaptación a pantallas pequeñas.
- `static/app.js`: validación en pantalla y conexión con el servidor.
- `test_app.py`: pruebas de reservas, RUT, contraseñas, sesiones, bloqueo, roles, registro y bitácora.

Ejecuta las pruebas con `python -m unittest -v`. Usan una base temporal y no modifican tus reservas.

## Alcance

Prototipo basado en el caso Resorts Altamar. Incluye inicio de sesión, registro de clientes, tres roles, validación de RUT, contraseñas con hash, bloqueo por intentos, bitácora de seguridad, servicios adicionales y comprobantes informativos. No incluye check-in/check-out, modificación de fechas de reservas, sugerencias de hoteles alternativos, adaptación por hotel físico, facturación ni procesamiento de pagos. Las cuentas de prueba son públicas en este README: sirven solo para la demostración. No está publicada en Internet y no representa el cumplimiento completo de la rúbrica EV03.

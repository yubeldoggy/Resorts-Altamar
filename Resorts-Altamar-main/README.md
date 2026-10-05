# Resorts Altamar - prototipo de reservas

Aplicación local para crear, listar, modificar fechas y cancelar reservas, con check-in/check-out y cobro, servicios adicionales por hotel, sugerencias de hoteles alternativos, estación de trabajo por hotel, inicio de sesión por RUT, registro de clientes y tres roles. Incluye 20 hoteles en 4 regiones, con 5 habitaciones de demostración por hotel. Los datos se guardan en `altamar.sqlite3`, creado al iniciar.

## Abrir

En Windows, haz doble clic en **INICIAR.bat**. Necesita Python 3.11 o posterior; no necesita instalar paquetes. El iniciador busca primero el comando `py` y luego `python`; si no encuentra ninguno, avisa que hay que instalar Python.

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

- **Cliente:** crea reservas a su nombre y solo ve, modifica fechas y cancela las suyas mientras estén confirmadas.
- **Recepción:** trabaja con el hotel de su estación (ver abajo): registra clientes, crea reservas para clientes con cuenta o huéspedes sin cuenta, modifica fechas, cancela, registra check-in y check-out, y agrega servicios a la cuenta del huésped.
- **Gerente:** ve las reservas de toda la cadena (con filtro por hotel), modifica fechas y cancela las confirmadas, registra check-in/check-out, configura los **servicios adicionales** de cada hotel y revisa la **actividad de seguridad**. No crea reservas.

Los permisos se validan en el servidor, no solo en la pantalla.

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
- **Errores:** los errores inesperados muestran un mensaje genérico, sin detalles internos. Los datos mal formados (JSON roto, números infinitos o gigantes, textos donde van números) reciben un 400 con un mensaje neutro, nunca un error de Python.
- **Números enviados por el usuario:** solo se aceptan enteros con dígitos 0-9 y dentro de un rango; se rechazan decimales, notación científica, hexadecimal y dígitos de otros alfabetos.
- **Límite de registros:** cada equipo puede intentar hasta 10 registros públicos cada 10 minutos. Frena la creación masiva de cuentas.
- **Fechas:** no se aceptan reservas con más de 2 años de anticipación.
- **Métodos HTTP no usados** (PUT, DELETE, etc.) responden 405 con las mismas cabeceras de seguridad.
- **Bitácora:** se registran inicios y cierres de sesión (con el hotel de la estación), accesos fallidos, bloqueos, registros de clientes, reservas, check-in/check-out con su cobro y cambios en los servicios. Solo gerencia puede verla.

**Límites conocidos:** la aplicación funciona con `http://` local, así que el tráfico entre el navegador y el servidor no va cifrado; para publicarla haría falta HTTPS. Tampoco incluye recuperación de contraseña. El bloqueo por intentos podría usarse para bloquear a propósito la cuenta de otra persona durante 5 minutos. El registro público avisa si un RUT ya tiene cuenta (el límite de 10 intentos lo frena, pero no lo elimina; evitarlo del todo requiere verificar identidad por correo).

El detalle de las pruebas de seguridad está en `PRUEBAS_SEGURIDAD.md`.

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

- `app.py`: servidor, base de datos, acceso (RUT, contraseñas, sesiones, bloqueo y roles), estación de trabajo, registro de clientes, servicios, cobros, bitácora y reglas de reservas.
- `estacion.json`: hotel físico de esta estación de trabajo.
- `static/index.html`: pantalla de acceso y registro, panel de reservas y bitácora.
- `static/style.css`: apariencia y adaptación a pantallas pequeñas.
- `static/app.js`: validación en pantalla y conexión con el servidor.
- `test_app.py`: pruebas de reservas, estación, servicios y cobros, RUT, contraseñas, sesiones, bloqueo, roles, registro, bitácora y ataques con datos maliciosos a través del servidor HTTP.
- `PRUEBAS_SEGURIDAD.md`: informe de las pruebas de seguridad, hallazgos y correcciones.

Ejecuta las pruebas con `python -m unittest -v`. Usan una base temporal y no modifican tus reservas.

## Reservas y recepción: demostración simple

- **Modificar fechas:** botón en las reservas confirmadas. Mantiene el hotel y el huésped, y asigna una habitación libre. Si las fechas no tienen cupo, no se pierde la reserva original.
- **Hoteles alternativos:** al intentar reservar sin cupo, muestra hoteles disponibles de la misma región. El cliente selecciona uno y vuelve a presionar Crear reserva; recepción los ve como información, porque solo reserva en el hotel de su estación. En una modificación fallida se muestran como información; cambiar de hotel requiere una nueva reserva.
- **Check-in:** recepción y gerencia pasan una reserva Confirmada a Alojado. Para probarlo, crea una reserva con llegada hoy y salida mañana. No permite ingresar antes de la llegada ni ocupar una habitación cuyo huésped aún no ha salido.
- **Check-out:** pasa de Alojado a Finalizada, calcula y guarda el cobro (ver *Servicios adicionales y cuenta*) y libera los cupos. Puede probarse inmediatamente después del check-in.
- Una reserva alojada, finalizada o cancelada no se puede modificar ni cancelar. Los nuevos movimientos quedan en la bitácora.
- Las estadísticas cuentan Confirmadas y Alojados como reservas activas; las Finalizadas no se cuentan como Canceladas. Hay filtros para cada estado.

No necesitas borrar la base de datos para usar estas funciones. Detén el servidor con Ctrl+C, vuelve a ejecutar INICIAR.bat y recarga el navegador.

## Estación de trabajo por hotel (RF-05 y RNF-03)

Cada computador de recepción representa una estación instalada en un hotel físico. El archivo `estacion.json` indica cuál:

```json
{
  "hotel": "Altamar Pucón"
}
```

Acepta el nombre del hotel, solo la ciudad (`"Valdivia"`) o su número (`11`). Al iniciar sesión, el personal ve un distintivo con el hotel detectado y el tiempo que tomó detectarlo (queda también en la bitácora). En nuestras pruebas tomó alrededor de 0,2 milisegundos; el requisito pide menos de 1 segundo.

- **Recepción** queda limitada a los datos locales: solo ve y opera reservas de ese hotel, y sus reservas nuevas se crean en ese hotel. Si no hay cupo, ve los hoteles alternativos de la región como información.
- **Gerencia** ve toda la cadena; el filtro por hotel parte en el hotel de la estación.
- **Clientes** no dependen de la estación: usan el portal desde cualquier lugar.

Para demostrar otro hotel, cambia el nombre en `estacion.json`, cierra sesión y vuelve a entrar (no hace falta reiniciar). Si el archivo falta o el hotel no existe, el inicio de sesión de recepción se rechaza. Gerencia conserva su acceso global.

## Servicios adicionales y cuenta (RF-04, RF-06 y RF-07)

- **Servicios por hotel (RF-07):** cada hotel parte con Spa, Tour guiado, Servicio a la habitación y Programa de millas. Gerencia agrega servicios, cambia nombre y precio, y los retira o reactiva desde su panel. Los servicios se guardan en la base de datos: los cambios rigen de inmediato, sin tocar el código ni detener el sistema.
- **Contratar servicios (RF-04):** el cliente abre **Servicios y cuenta** en su reserva confirmada o alojada y agrega servicios (1 a 20 unidades), o los quita antes del check-out. Recepción y gerencia pueden hacer lo mismo por el huésped.
- **Cobro en el check-out (RF-06):** total = noches × tarifa por noche + servicios contratados. Al hacer check-out se guarda el total cobrado y queda visible en la reserva y en la bitácora.
- La tarifa y el precio de cada servicio se copian al reservar o contratar: si gerencia cambia un precio después, las cuentas ya abiertas no cambian.
- **Tarifas de demostración (no son precios reales):** Norte $85.000, Centro $95.000, Sur $90.000 y Austral $110.000 por noche.

El cobro es un registro del monto: no hay pago en línea, boleta ni factura.

## Alcance

Prototipo basado en el caso Resorts Altamar. Cubre, en versión de prototipo, RF-01 a RF-07 y los requisitos RNF-02 a RNF-04. La arquitectura actual y la propuesta SaaS/IaaS (RNF-01), junto con los diagramas UML, están explicadas en [docs/LEEME.md](docs/LEEME.md). La propuesta cloud no está desplegada.

Límites: funciona en un solo computador con `http://` local (sin HTTPS), el cobro no incluye pago real, y las cuentas de prueba son públicas en este README: sirven solo para la demostración.


## Material para presentar EV03

Abre [docs/LEEME.md](docs/LEEME.md): arquitectura, tres diagramas UML, seguridad, resultado de 58 pruebas y guion para tres integrantes. La documentación distingue lo implementado de lo propuesto y no afirma certificación ISO ni cumplimiento legal integral.

## Mejoras de la demostración (3 de octubre de 2026)

- **Consultar disponibilidad:** selecciona hotel y fechas y pulsa el botón, sin necesidad de crear una reserva ni introducir un huésped. El cupo se comprueba de nuevo al confirmar.
- **Actualización automática:** las reservas y sus estadísticas se consultan cada cinco segundos mientras la pestaña está visible. Se pausa con un diálogo abierto o mientras se usan los botones de una reserva. El botón Actualizar sigue disponible. Esto funciona entre sesiones conectadas al mismo servidor; no sincroniza instalaciones independientes.
- **Estación obligatoria:** recepción no puede iniciar sesión sin un hotel válido en `estacion.json`. Las sesiones antiguas sin hotel dejan de ser aceptadas.

## Datos de la reserva (5 de octubre de 2026)

- **Personas:** cada habitación tiene **4 puestos de adulto** (18 años o más). Cada puesto que no usa un adulto admite **2 niños** (de 2 a 17 años): 4 adultos y 0 niños, 3 y 2, 2 y 4, o 1 y 6. Además, hasta **2 bebés** menores de 2 años en cuna, que no ocupan puesto. Siempre debe viajar al menos 1 adulto: niños y bebés no pueden reservar solos. La tarifa por noche no cambia según la cantidad de personas.
- **Contacto:** teléfono chileno de 9 dígitos, celular (empieza con 9, por ejemplo `+56 9 1234 5678`) o fijo (empieza con su código de área, por ejemplo `+56 2 2421 3146`); se rechazan los que empiezan con 0, 1 u 8. Se guarda como `+56XXXXXXXXX`. También se pide correo electrónico; ambos son obligatorios. Se validan en el navegador y de nuevo en el servidor.
- El teléfono y el correo **no se escriben en la bitácora**; allí solo queda la cantidad de personas.
- Las reservas creadas antes de este cambio no tienen estos datos y se muestran sin esa línea.
- **Consultar disponibilidad sin cupo (RF-03):** si el hotel no tiene habitaciones para esas fechas, se muestran automáticamente los hoteles de la misma región con cupo. El cliente puede elegir uno y presionar Crear reserva.

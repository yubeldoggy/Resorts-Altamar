# Exposición sencilla para tres integrantes

Propuesta de 6 a 8 minutos, ajustable al tiempo que asigne el docente. Practiquen todos el flujo completo y repartan también las respuestas.

## Integrante 1: acceso, seguridad y arquitectura (2 minutos)

1. Iniciar sesión y explicar cliente, recepción y gerencia.
2. Abrir `ARQUITECTURA.md` y mostrar navegador, Python y SQLite.
3. Decir: «Actualmente funciona localmente. Proponemos ofrecer Altamar como SaaS; IaaS sería una opción para alojarlo en una máquina virtual. Aún no está en la nube».
4. Mencionar hash de contraseñas, permisos del servidor y bitácora como medidas de seguridad relacionadas con OWASP y prevención de accesos indebidos.

## Integrante 2: reservas y UML (2 a 3 minutos)

1. Crear una reserva con llegada hoy y salida mañana.
2. Mostrar el cambio de fechas y explicar que un fallo conserva la reserva original.
3. Mostrar un hotel lleno y sus alternativas regionales. Preparar previamente cinco reservas para el mismo hotel y fechas; usar una cuenta cliente para seleccionar un hotel alternativo.
4. Como recepción, registrar check-in. Mostrar el diagrama de estados: Confirmada → Alojado → Finalizada.
5. Explicar en el diagrama de secuencia: «La base bloquea la escritura antes de comprobar y asignar el cupo».

## Integrante 3: servicios, cobro y pruebas (2 a 3 minutos)

1. Agregar un servicio a la estadía y mostrar su cuenta.
2. Registrar check-out: total = noches × tarifa + servicios. Es un registro de demostración, no un pago real ni factura.
3. Mostrar `RESULTADO_PRUEBAS.txt`: 47 pruebas aprobadas en la revisión del 1 de octubre de 2026.
4. Abrir `SEGURIDAD_Y_PRUEBAS.md` y señalar una corrección con su prueba de regresión.
5. Decir: «Usamos prácticas de confidencialidad, integridad y disponibilidad. No contamos con certificación ISO ni una auditoría legal completa».

## Preparación

- Ejecutar el sistema desde **Resorts-Altamar-main**, no desde la carpeta superior con la versión anterior.
- Usar las cuentas del README de esa versión. Revisar el hotel de `estacion.json`; recepción opera ese hotel.
- Para check-in, la fecha actual debe estar dentro de la estadía. Evitar fechas fijas de capturas anteriores.
- Abrir `UML.md` en GitHub para ver los diagramas. Tener abiertos arquitectura y resultado de pruebas.
- No borrar bases de datos para preparar la exposición: usar reservas de demostración identificables.

## Preguntas y respuestas breves

**¿Es SaaS o IaaS?** El prototipo corre localmente. SaaS es el modelo de aplicación propuesto, e IaaS una infraestructura posible.

**¿Cómo evitan la sobreventa?** Consultan y reservan dentro de una transacción; la clave única de noches evita duplicar la habitación.

**¿Las 47 pruebas demuestran que nunca falla?** No. Cubren los casos definidos, incluyendo algunos escenarios concurrentes y de seguridad.

**¿La estación detecta GPS?** No. Lee una configuración del equipo; es una simulación sencilla del contexto físico del hotel.

**¿Cumplen ISO y la ley?** Mostramos medidas y evidencia relacionadas; no afirmamos certificación ni cumplimiento integral.

**¿Aplicaron todos los patrones del informe inicial?** Esta implementación se simplificó con funciones y una base central. No presentamos patrones no implementados como si existieran.

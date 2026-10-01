# Seguridad y pruebas - evidencia para la exposición

## Resultado comprobado

El **1 de octubre de 2026** se ejecutó la suite de `Resorts-Altamar-main`: **47 pruebas aprobadas, 0 fallos y 0 errores**, en 8,264 segundos en este equipo. La salida íntegra está en [RESULTADO_PRUEBAS.txt](RESULTADO_PRUEBAS.txt).

Se utilizó una copia del código y bases temporales; no se modificaron las reservas reales. Las pruebas reducen las iteraciones de hash para ejecutarse más rápido. Este tiempo no representa una medición de rendimiento de producción.

Identificación del código revisado (SHA-256):

```text
app.py
967d9458b81348f75b86d22f22252321becbaf6230e863130192adcc6d8b21e1
test_app.py
6a4c34c749530692de4f4a146f4fd3b2cc3a53f4803c7b9cb825ddd57ffc8cae
```

Para repetir desde la carpeta que contiene esta versión de `app.py`:

```powershell
py -3 -m unittest -v
```

El informe existente `PRUEBAS_SEGURIDAD.md` relata una campaña anterior de 1.006 peticiones y resultados antes/después. **Esa campaña no se repitió aquí**: este registro confirma únicamente la suite actual de 47 pruebas. No confundir pruebas automáticas con una auditoría completa.

## Controles presentes y evidencia

| Riesgo | Implementación | Prueba o evidencia del proyecto |
|---|---|---|
| Acceso a reservas ajenas | Sesión, rol y propietario validados en servidor. | `test_client_permissions`, `test_client_only_sees_and_cancels_own_reservations` |
| Operar otro hotel | Restricción de recepción cuando la estación es válida. | `test_reception_works_only_with_local_hotel` |
| Exposición de contraseñas | PBKDF2-SHA256 con sal; hash de tokens de sesión. | `test_passwords_are_stored_hashed`, revisión de `start_session` |
| Intentos repetidos de acceso | Bloqueo temporal por RUT. | `test_account_locks_after_five_failures` |
| Entradas malformadas y SQL | Validación numérica, consultas parametrizadas y errores controlados. | `test_weird_ids_never_cause_500`, `test_malformed_bodies_get_generic_400` |
| HTML introducido por usuarios | `escapeHTML` en la interfaz y validación de nombres. | Revisión de `static/app.js`; no se repitió una prueba XSS en navegador en esta revisión. |
| Solicitudes desde otro sitio | Comprobación de Host/Origin, JSON y cookie SameSite. | `test_cross_site_requests_rejected` |
| Sobreventa | Transacción y clave única por hotel/habitación/noche. | `test_concurrent_requests_cannot_overbook` |
| Pérdida de reserva al cambiar fechas | Rollback si no existe cupo. | `test_failed_modification_preserves_original` |
| Cobro incorrecto | Noches por tarifa más servicios; precios copiados al contratar. | `test_client_contracts_and_checkout_charges` |

Estos controles se pueden explicar con [OWASP ASVS](https://owasp.org/projects/asvs?tab=main), que ofrece criterios para verificar seguridad de aplicaciones. No se ha evaluado aquí el cumplimiento completo de un nivel ASVS.

## Correcciones: cómo mostrarlas sin inventar evidencia

El informe previo documenta validación de números, errores HTTP, fechas máximas y límites de registro. En la versión actual se pueden mostrar estas regresiones:

| Corrección documentada previamente | Función actual | Prueba ejecutada ahora |
|---|---|---|
| Rechazar IDs fuera de rango o no numéricos | `to_int` | `test_to_int_rejects_weird_values` |
| Ocultar detalles de errores JSON | `Handler.do_POST` | `test_malformed_bodies_get_generic_400` |
| Métodos no admitidos con respuesta controlada | `Handler` | `test_other_methods_and_paths` |
| Rechazar fechas demasiado lejanas | `check_stay` | `test_reservation_too_far_ahead` |
| Limitar registros públicos | `allow_registration` | `test_registration_rate_limit` |

Estas pruebas verifican el comportamiento actual; por sí solas no reproducen los fallos de la versión anterior.

## Relación sencilla con ISO 27000

La familia ISO/IEC 27000 trata la seguridad de la información. [ISO/IEC 27001](https://www.iso.org/standard/27001) establece requisitos de un sistema de gestión de seguridad, que abarca personas, procesos y tecnología.

- **Confidencialidad:** acceso por usuario y rol; las contraseñas se almacenan con hash.
- **Integridad:** transacciones, restricciones y pruebas de reglas de negocio.
- **Disponibilidad:** manejo de errores y pruebas concurrentes limitadas. No hay alta disponibilidad ni respaldos automáticos implementados.
- **Mejora:** registrar un hallazgo, corregirlo y conservar una prueba de regresión.

Frase para exponer: «Aplicamos prácticas relacionadas con seguridad de la información y mostramos evidencia técnica. No afirmamos estar certificados ni cumplir integralmente ISO 27001».

## Ley chilena 21.459

La [Ley 21.459, texto en BCN](https://www.bcn.cl/leychile/Navegar?idNorma=1177743&idVersion=2025-01-01) regula delitos informáticos. Para la demostración, relacionen la prevención de accesos no autorizados y alteración de información con los permisos, validaciones y registros del sistema. Usen datos de prueba y realicen las pruebas sobre sistemas autorizados.

La relación anterior es una explicación académica de medidas preventivas: el código y las pruebas no constituyen una certificación de cumplimiento legal.

## Límites que deben reconocer

HTTP local sin HTTPS; cuentas de demostración conocidas; archivo SQLite sin cifrado añadido por la aplicación; ausencia de copias automáticas y monitoreo; bloqueo por RUT susceptible de abuso. Si la estación falta, recepción pierde la restricción local en esta implementación. Mantener `estacion.json` correcto para la demo; antes de producción debería rechazarse ese inicio de sesión.

No se midieron 300 usuarios concurrentes, disponibilidad anual ni tolerancia a fallos. Las fuentes oficiales se consultaron el 1 de octubre de 2026.

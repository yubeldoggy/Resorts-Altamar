# Diagramas UML - versión simplificada

Diagramas alineados con `Resorts-Altamar-main/app.py`. Se visualizan en GitHub. Los nombres de entidades representan tablas; no afirman que existan clases Python para cada una.

## 1. Estructura: modelo de dominio

```mermaid
classDiagram
    class Usuario {
        id: int
        rut: str
        name: str
        role: str
        password_hash: str
    }
    class Hotel {
        id: int
        name: str
        region: str
        rate: int
    }
    class Reserva {
        id: int
        guest: str
        adults: int
        children: int
        infants: int
        phone: str
        email: str
        arrival: date
        departure: date
        room: int
        status: str
        rate: int
        total: int
    }
    class NocheOcupada {
        hotel_id: int
        room: int
        night: date
        reservation_id: int
    }
    class Servicio {
        id: int
        name: str
        price: int
        active: bool
    }
    class ServicioContratado {
        id: int
        name: str
        price: int
        quantity: int
    }
    Usuario "0..1" --> "0..*" Reserva : titular registrado
    Hotel "1" --> "0..*" Reserva : recibe
    Reserva "1" --> "0..*" NocheOcupada : bloquea cupos
    Hotel "1" --> "0..*" Servicio : ofrece
    Reserva "1" --> "0..*" ServicioContratado : incluye
    Servicio "1" --> "0..*" ServicioContratado : origina
```

Correspondencia: `Usuario=users`, `Hotel=hotels`, `Reserva=reservations`, `NocheOcupada=nights`, `Servicio=services`, `ServicioContratado=reservation_services`. No existe tabla Habitación: `room` identifica una de cinco habitaciones por hotel. `region` es un atributo del hotel. Se omiten sesiones y bitácora para mantener legible el modelo de negocio.

`user_id` admite nulo por compatibilidad con reservas antiguas. En una reserva nueva de huésped sin cuenta, el código asigna como propietario al recepcionista creador. Por eso Usuario no equivale siempre al huésped. El precio se copia en cada contratación para conservarlo aunque cambie el catálogo. `adults`, `children`, `infants`, `phone` y `email` quedan vacíos en reservas creadas antes del 5 de octubre de 2026.

## 2. Interacción: crear reserva sin sobreventa

```mermaid
sequenceDiagram
    actor U as Cliente o recepcionista
    participant V as Interfaz web
    participant H as Handler
    participant R as create_reservation
    participant D as SQLite
    U->>V: Completar hotel, fechas, personas y contacto
    V->>H: POST /api/reservations
    H->>D: Verificar sesión
    D-->>H: Usuario autorizado
    H->>R: Datos y usuario
    R->>R: Validar rol, fechas, hotel de estación, personas (máx. 4) y contacto
    R->>D: BEGIN IMMEDIATE
    R->>D: Consultar noches ocupadas
    D-->>R: Habitaciones ocupadas
    alt Hay habitación libre
        R->>D: INSERT reserva y noches + bitácora
        R->>D: COMMIT
        R-->>H: Código y habitación
        H-->>V: HTTP 200
        V-->>U: Reserva confirmada
    else No hay cupo
        R->>D: Buscar hoteles libres de la misma región
        D-->>R: Alternativas disponibles
        R->>D: ROLLBACK
        R-->>H: NoAvailability con alternativas
        H-->>V: HTTP 409 con alternativas
        V-->>U: Mostrar hoteles alternativos
    end
```

Funciones: `create_reservation`, `available_room`, `alternative_hotels` y `connection`. La transacción mantiene unidas la consulta y la asignación. Una sugerencia no bloquea el hotel alternativo: la disponibilidad se vuelve a validar al confirmar.

## 3. Comportamiento: estados de una reserva

```mermaid
stateDiagram-v2
    [*] --> Confirmada : crear con cupo
    Confirmada --> Confirmada : modificar fechas con cupo
    Confirmada --> Cancelada : cancelar
    Confirmada --> Alojado : check-in autorizado durante la estadía
    Alojado --> Finalizada : check-out autorizado
    Cancelada --> [*]
    Finalizada --> [*]
```

Cancelar libera las noches. El check-out calcula alojamiento y servicios, guarda el total y libera las noches. Una reserva alojada, cancelada o finalizada no admite modificación de fechas ni cancelación. Solo recepción y gerencia hacen check-in/check-out; el cliente administra sus propias reservas.

## Roles y funciones para explicar los diagramas

| Actor | Operaciones |
|---|---|
| Cliente | Crear y consultar sus reservas, modificar fechas, cancelar y contratar servicios. |
| Recepción | Registrar clientes y gestionar reservas, servicios y entradas/salidas en el hotel configurado. |
| Gerencia | Supervisar la cadena, configurar servicios, gestionar reservas existentes y consultar bitácora. No crea reservas. |

Recepción necesita una estación válida para iniciar sesión y solo opera en ese hotel.

# Uso e impacto de los endpoints de operaciones de `pf-payroll`

**Última actualización:** 2026-10-03
**Estado:** actualizado después de retirar estas tres superficies HTTP y CLI.
**Alcance:** ecosistema `pf-*` completo (`pf-base`, `pf-payroll`, `pf-rates`, `pf-sheets`, `pf-db`)

## Estado actual

Este documento reemplaza el snapshot del 2026-10-02. La recomendación anterior quedó
obsoleta después de su implementación parcial.

La superficie HTTP actual de operaciones es:

| Endpoint | HTTP actual | CLI/use case actual | Estado |
| --- | --- | --- | --- |
| `POST /payroll/{period_id}/assign-plans` | No | No | Eliminado |
| `POST /payroll/{period_id}/compute-contributions` | No | No; servicio interno conservado | Eliminado como wrapper |
| `POST /payroll/{period_id}/compute-tax` | No | No; use case interno conservado | Eliminado como adaptador |
| `POST /payroll/{period_id}/review` | No | No | Eliminado |
| `POST /payroll/{period_id}/deflate` | No | No dedicado; lógica interna relacionada permanece | Eliminado como HTTP |

La fuente de verdad para las rutas es:

```text
modules/pf-payroll/src/payroll/interfaces/api/routes/payroll.py
```

La colección Postman y `docs/api.md` deben coincidir con esta tabla.

## Qué cambió respecto del análisis anterior

El análisis original recomendaba eliminar `assign-plans`, `compute-contributions`,
`compute-tax` y `deflate`, y mantener `review`. La implementación realizada no siguió
esa recomendación literalmente:

- `assign-plans` fue eliminado por completo, incluyendo su use case, DTOs,
  método de repositorio, comando CLI, ruta, tests y request Postman.
- `compute-contributions` perdió su wrapper HTTP/CLI. `ContributionComputationService`
  y los DTOs de cálculo se conservaron porque el post-procesamiento de importación los usa.
- `compute-tax` perdió sus adaptadores HTTP/CLI. `ComputeIncomeTax` se conservó porque
  `ProcessImportedPayrollPeriods` lo ejecuta automáticamente.

Por lo tanto, el documento anterior debe interpretarse como evidencia histórica y no
como una descripción del sistema actual.

## Evidencia histórica de producción

En la ventana de logs consultada el 2026-10-02 se observó:

| Endpoint | Requests observados | Resultado |
| --- | ---: | --- |
| `assign-plans` | 0 | Sin requests observados |
| `compute-contributions` | 0 | Sin requests observados |
| `compute-tax` | 0 | Sin requests observados |
| `review` | 2 | 1 × `200`, 1 × `404` |
| `deflate` | 1 | 1 × `404` |

El request exitoso de `review` fue:

```text
POST /payroll/548/review → 200
User-Agent: PostmanRuntime/2.7.0
Timestamp: 2026-09-28T19:39:55Z
```

Ese tráfico explica por qué la recomendación original proponía mantener `review`, pero
la decisión posterior fue eliminar ese workflow. La evidencia histórica no debe
confundirse con la superficie HTTP actual.

## Consumidores dentro del ecosistema

No se encontraron llamadas desde `pf-rates`, `pf-sheets` ni `pf-db` hacia estas rutas.

- `pf-rates` no consume `pf-payroll`.
- `pf-sheets` usa export CSV desde `pf-rates` y la pestaña local `VALUES`.
- `pf-db` contiene DDL, migraciones y seeds; no consume HTTP.
- Los comandos CLI y los flujos internos de `pf-payroll` no son consumidores HTTP.

La ausencia de consumidores internos sigue siendo cierta, pero no demuestra por sí sola
que una ruta administrativa no tenga uso manual externo al repositorio.

## Evaluación actual por endpoint

### `assign-plans`

Fue eliminado de HTTP, CLI, Postman y de la capa de aplicación. La importación no usa
este use case eliminado: asigna planes complementarios mediante
`ComplementaryInsuranceService`, que es una operación distinta y permanece activa.

**Estado actual:** eliminado completamente.

### `compute-contributions`

El wrapper HTTP/CLI fue eliminado. La lógica reutilizable no fue eliminada:
`ContributionComputationService` sigue siendo instanciado por
`ProcessImportedPayrollPeriods` durante el procesamiento automático de importaciones.

**Estado actual:** adaptadores HTTP/CLI eliminados; servicio interno conservado.

### `compute-tax`

Los adaptadores HTTP y CLI fueron eliminados. `ComputeIncomeTax` permanece como use case
interno y es instanciado y ejecutado por `ProcessImportedPayrollPeriods` cuando el período
importado cumple las precondiciones.

**Estado actual:** adaptadores HTTP/CLI eliminados; use case interno conservado.

### `review`

Ya no existe como ruta HTTP ni como comando CLI. También se eliminó el workflow de
revisión y los contratos asociados a `PAY_PERIOD.status`.

La evidencia histórica de un request `200` queda registrada únicamente como contexto
de migración. No debe agregarse nuevamente a Postman ni a `docs/api.md` sin una nueva
decisión de diseño.

**Estado actual:** eliminado intencionalmente.

### `deflate`

Ya no existe como endpoint HTTP ni request Postman. El use case interno
`DeflateAmounts` permanece porque todavía forma parte de lógica de aplicación usada por
otros flujos, aunque no tiene una interfaz HTTP dedicada.

No hubo evidencia histórica de uso HTTP exitoso; el único request observado terminó en
`404`.

**Estado actual:** eliminado de HTTP; no eliminar todavía el use case interno sin una
revisión separada de sus call sites.

## Recomendación vigente

La decisión actual no debe formularse como “eliminar cuatro endpoints y mantener
review”. Esa recomendación ya fue parcialmente ejecutada de otra manera.

La recomendación anterior queda reemplazada por la implementación realizada:

1. eliminar completamente `assign-plans`;
2. eliminar el wrapper `ComputeContributions`, pero conservar
   `ContributionComputationService`;
3. eliminar únicamente los adaptadores HTTP/CLI de `compute-tax`, conservando
   `ComputeIncomeTax`;
4. conservar el post-procesamiento automático de importación;
5. no reintroducir requests Postman para estas operaciones.

## Checklist de consistencia actual

- `review` no debe aparecer como endpoint en `docs/api.md` ni Postman.
- `deflate` no debe aparecer como endpoint en `docs/api.md` ni Postman.
- `assign-plans`, `compute-contributions` y `compute-tax` no deben aparecer como
  endpoints ni comandos CLI en `docs/api.md` ni Postman.
- `ContributionComputationService` debe seguir cubierto por tests y usado por la
  importación automática.
- `ComputeIncomeTax` debe seguir cubierto por tests y usado por la importación automática.
- La evidencia histórica de logs queda solo como contexto, no como inventario actual.

## Conclusión

El documento describe ahora el estado verificado después de retirar las tres superficies
HTTP/CLI. Las rutas de operaciones analizadas no aparecen en el OpenAPI actual ni en
Postman; solo permanecen servicios/use cases internos donde el flujo de importación los
necesita.

La conclusión actual es: las tres operaciones ya no tienen superficie HTTP ni CLI.
`assign-plans` fue eliminado entero. El servicio interno de contribuciones y el use case
interno de impuesto permanecen porque los necesita el flujo automático de importación.
`review` y `deflate` HTTP también continúan retirados.

# Uso e impacto de los endpoints `pf-payroll / Periods / Operations`

**Fecha:** 2026-10-02
**Alcance:** ecosistema `pf-*` completo (`pf-base`, `pf-payroll`, `pf-rates`,
`pf-sheets`, `pf-db`)
**Conclusión:** dentro del ecosistema, solo `review` tiene evidencia de uso HTTP
exitoso en producción. Los otros endpoints existen, están documentados y tienen
alternativas CLI o lógica interna relacionada, pero no tienen evidencia de tráfico
HTTP exitoso en los logs disponibles.

> Este análisis no considera consumidores fuera del ecosistema `pf-*`, porque el uso
> objetivo y permitido de estas rutas se limita a este ecosistema.

## 1. Endpoints analizados

La carpeta `pf-payroll / Periods / Operations` de la colección Postman contiene:

| Endpoint | Propósito |
| --- | --- |
| `POST /payroll/{period_id}/assign-plans` | Asignar snapshots de AFP y salud a un período. |
| `POST /payroll/{period_id}/compute-contributions` | Calcular cotizaciones previsionales y seguro de cesantía. |
| `POST /payroll/{period_id}/compute-tax` | Calcular impuesto único mensual. |
| `POST /payroll/{period_id}/review` | Marcar un período como `reviewed`. |
| `POST /payroll/{period_id}/deflate` | Convertir montos nominales a CLP real mediante un índice económico. |

Las rutas están implementadas en:

```text
modules/pf-payroll/src/payroll/interfaces/api/routes/payroll.py
```

## 2. Método de clasificación

Cada endpoint se clasificó usando tres niveles de evidencia dentro del ecosistema:

1. **Uso HTTP productivo observado:** request real en los logs de Cloud Run de
   `pf-payroll`.
2. **Uso directo documentado o disponible:** aparece en Postman, documentación o CLI,
   pero no necesariamente tiene tráfico HTTP productivo observado.
3. **Uso automático interno:** la lógica de negocio equivalente se ejecuta desde el
   flujo de importación u otro caso de uso, pero sin llamar al endpoint HTTP.

Es importante distinguir estos niveles. Que exista un use case o que el flujo de
importación haga un cálculo relacionado **no significa que el endpoint HTTP sea usado**.
Los endpoints HTTP de Operations no se llaman entre sí.

## 3. Resultado resumido

| Endpoint | HTTP productivo observado | Referencia directa en el ecosistema | Uso automático equivalente | Estado recomendado |
| --- | --- | --- | --- | --- |
| `assign-plans` | No | Postman, docs, CLI | Sí, importación puede recibir o deducir planes; también asigna planes complementarios | Mantener o reemplazar explícitamente |
| `compute-contributions` | No | Postman, docs, CLI | Sí, importación refresca/recalcula contribuciones cuando corresponde | Mantener como operación manual |
| `compute-tax` | No | Postman, docs, CLI | Sí, importación calcula impuesto cuando corresponde | Mantener como operación manual |
| `review` | **Sí: 1 request HTTP `200`** | Postman, docs, CLI | No es reemplazado por importación | **Mantener** |
| `deflate` | No; solo un intento `404` | Postman, docs | No | Candidato principal a retirar |

## 4. Evidencia dentro del ecosistema

### 4.1 `pf-base` / Postman

Los cinco endpoints están presentes en:

```text
postman/pf-ecosystem.postman_collection.json
```

Su presencia significa que forman parte de la interfaz operativa documentada del
ecosistema, pero por sí sola no prueba que cada request se ejecute con frecuencia.

### 4.2 `pf-payroll` / CLI

Los siguientes endpoints tienen una alternativa CLI dentro de `pf-payroll`:

```text
assign-plans
compute-contributions
compute-tax
review
```

El CLI no tiene un comando equivalente dedicado para `deflate`.

Por lo tanto, al retirar una ruta HTTP:

- los cuatro primeros conservan una vía operativa local mediante CLI;
- `deflate` perdería su única interfaz operativa explícita identificada, aparte de
  invocar código internamente.

### 4.3 `pf-rates`, `pf-sheets` y `pf-db`

No se encontraron llamadas desde estos repositorios hacia los endpoints de
`pf-payroll / Periods / Operations`.

En particular:

- `pf-rates` no consume `pf-payroll`.
- `pf-sheets` trabaja con su propio flujo de export CSV y la pestaña local `VALUES`.
- `pf-db` solo posee esquema/migraciones y no consume HTTP.

## 5. Evidencia de producción en Cloud Run

Se consultaron los logs disponibles del servicio:

```text
Proyecto: coreassistant-474022
Servicio: pf-payroll
```

El tráfico observado para las rutas de Operations fue:

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

También se observó:

```text
POST /payroll/152/review → 404
POST /payroll/1/deflate → 404
```

### Interpretación

- `review` es el único endpoint con uso HTTP productivo exitoso comprobado.
- `assign-plans`, `compute-contributions` y `compute-tax` no tienen tráfico HTTP
  observado en la ventana de logs disponible.
- `deflate` tampoco tiene uso exitoso observado; solo existe un intento contra un
  período inexistente.
- La ausencia de tráfico no significa que la operación sea conceptualmente inútil;
  significa que no se observó el endpoint HTTP ejecutándose en producción.

## 6. Análisis individual

### 6.1 `assign-plans`

#### Uso identificado dentro del ecosistema

- Disponible en Postman.
- Documentado en `docs/api.md` y `docs/payroll-workflow.md`.
- Disponible como comando CLI.
- La importación puede recibir planes explícitos o deducirlos.
- El post-procesamiento de importación asigna planes de seguro complementario cuando
  corresponde.
- No hay request HTTP productivo observado.
- Ningún otro servicio `pf-*` llama este endpoint.

#### Impacto de dejar de exponerlo

Se perdería la corrección manual vía HTTP de los planes de un período ya importado.
La importación seguiría funcionando cuando los planes puedan venir en el archivo o
ser deducidos, y el CLI seguiría siendo una alternativa.

#### Evaluación

**Endpoint HTTP no observado en uso, pero operación administrativa válida.**

Puede retirarse de la colección Postman o moverse a una carpeta administrativa sin
riesgo para el flujo automático. Para retirar la ruta del servicio, primero habría
que confirmar que el CLI es suficiente para las correcciones manuales.

### 6.2 `compute-contributions`

#### Uso identificado dentro del ecosistema

- Disponible en Postman.
- Documentado.
- Disponible como comando CLI.
- La importación refresca/recalcula contribuciones cuando se cumplen sus condiciones.
- No hay request HTTP productivo observado.
- Ningún otro servicio `pf-*` llama este endpoint.

#### Impacto de dejar de exponerlo

Se perdería el recálculo manual HTTP de:

- `PENSION_BASE`
- `PENSION_ADDITIONAL`
- `HEALTH_BASE`
- `HEALTH_ADDITIONAL_UF`
- `UNEMPLOYMENT_INSURANCE`

El cálculo automático de importación y el CLI seguirían disponibles.

#### Evaluación

**Endpoint HTTP no observado en uso productivo, pero útil como herramienta de
reparación.** Mantenerlo como operación manual o reemplazarlo formalmente por otra
interfaz antes de retirarlo.

### 6.3 `compute-tax`

#### Uso identificado dentro del ecosistema

- Disponible en Postman.
- Documentado.
- Disponible como comando CLI.
- La importación calcula impuesto cuando se cumplen sus precondiciones.
- No hay request HTTP productivo observado.
- Ningún otro servicio `pf-*` llama este endpoint.

#### Impacto de dejar de exponerlo

Se perdería el recálculo manual HTTP de `INCOME_TAX` después de modificar:

- ingreso tributable
- cotizaciones deducibles
- UTM
- tramos tributarios
- datos del período

El flujo automático y el CLI seguirían disponibles.

#### Evaluación

**Endpoint HTTP no observado en uso productivo, pero útil como recuperación manual.**
Puede ocultarse de la colección normal, pero retirarlo de la API requeriría validar que
el CLI cubre completamente la operación administrativa.

### 6.4 `review`

#### Uso identificado dentro del ecosistema

- Tiene un request exitoso en producción.
- Está disponible en Postman.
- Está documentado.
- Está disponible como comando CLI.
- No se encontró otro endpoint HTTP que marque el período como `reviewed`.
- La importación no reemplaza esta transición de estado.

#### Impacto de dejar de exponerlo

Se perdería la acción HTTP que confirma/revisa un período. Los períodos podrían seguir
siendo importados y calculados, pero no habría una transición equivalente vía API hacia
`reviewed`.

#### Evaluación

**Usado y funcionalmente único dentro del ecosistema. Mantener.**

### 6.5 `deflate`

#### Uso identificado dentro del ecosistema

- Disponible en Postman.
- Documentado.
- No tiene comando CLI dedicado.
- No es utilizado por los flujos automáticos de importación/revisión.
- No hay request HTTP exitoso observado; solo un intento `404`.
- Ningún otro servicio `pf-*` llama este endpoint.

#### Impacto de dejar de exponerlo

Se perdería la capacidad de obtener bajo demanda los valores reales de:

- taxable income
- gross income
- total discounts
- net pay

El endpoint es de lectura y no es necesario para persistir ni revisar un período.

#### Evaluación

**Es el candidato más claro para retirar de la superficie HTTP y de Postman.**
Antes de borrarlo conviene confirmar que no se usa manualmente para reporting local, pero
no hay evidencia de uso productivo en el ecosistema.

## 7. Recomendación drástica

La regla aplicada es:

> Si un endpoint HTTP no tiene consumidores dentro del ecosistema `pf-*` y no tiene
> tráfico HTTP productivo observado, se elimina de la superficie HTTP y de Postman.

Esta regla se aplica al **endpoint HTTP**, no necesariamente a la lógica interna ni al
comando CLI que pueda seguir siendo útil.

### 7.1 Eliminar de la API y de Postman

#### `POST /payroll/{period_id}/assign-plans`

Eliminar porque:

- no tiene tráfico HTTP productivo observado;
- ningún otro repositorio `pf-*` lo consume;
- la operación está disponible por CLI;
- la importación puede recibir o deducir los planes;
- no es invocada automáticamente por otro endpoint HTTP.

Se mantiene `AssignPlans` como use case y el comando CLI, porque ambos sí forman parte
de la superficie operativa interna de `pf-payroll`.

#### `POST /payroll/{period_id}/compute-contributions`

Eliminar porque:

- no tiene tráfico HTTP productivo observado;
- ningún otro repositorio `pf-*` lo consume;
- la operación está disponible por CLI;
- el procesamiento de importación ya calcula/refresca contribuciones cuando corresponde;
- el use case sigue siendo utilizado por el flujo interno y el CLI.

Se mantiene la lógica de cálculo y el comando CLI. Se elimina únicamente el adaptador
HTTP y su request Postman.

#### `POST /payroll/{period_id}/compute-tax`

Eliminar porque:

- no tiene tráfico HTTP productivo observado;
- ningún otro repositorio `pf-*` lo consume;
- la operación está disponible por CLI;
- el procesamiento de importación ya calcula impuesto cuando corresponde;
- el use case sigue siendo utilizado por el flujo interno y el CLI.

Se mantiene la lógica de cálculo y el comando CLI. Se elimina únicamente el adaptador
HTTP y su request Postman.

#### `POST /payroll/{period_id}/deflate`

Eliminar porque:

- no tiene tráfico HTTP exitoso observado;
- el único request observado terminó en `404`;
- ningún otro repositorio `pf-*` lo consume;
- no tiene comando CLI equivalente;
- no forma parte del flujo automático de importación o revisión;
- su función es un cálculo read-only de análisis, no una operación necesaria para
  persistir o procesar payroll.

En este caso no queda una interfaz operativa conocida dentro del ecosistema. El use case
puede eliminarse junto con la ruta si no existe una necesidad de reporting local no
registrada en el repositorio. Antes del borrado físico del use case, se deben remover sus
tests y referencias de dependencias.

### 7.2 Mantener

#### `POST /payroll/{period_id}/review`

Mantener porque:

- tiene un request HTTP exitoso observado en producción;
- está incluido en el workflow operativo documentado;
- está disponible en Postman y CLI;
- no existe otro endpoint HTTP que realice la transición a `reviewed`;
- la importación no reemplaza esta confirmación explícita.

Este endpoint es el único de la carpeta Operations con evidencia directa de uso HTTP
exitoso y una responsabilidad que no está cubierta por otra interfaz.

## 8. Impacto esperado de la eliminación

### Impacto funcional

Se elimina la posibilidad de invocar esas cuatro operaciones mediante HTTP desde la
colección Postman o cualquier otro componente del ecosistema.

No se elimina:

- el cálculo interno de contribuciones;
- el cálculo interno de impuestos;
- la asignación de planes mediante CLI;
- el proceso automático de importación;
- la revisión de períodos mediante HTTP/CLI.

### Impacto sobre Postman

La carpeta `Periods / Operations` quedará reducida a:

```text
Review period
```

Las requests de las cuatro operaciones eliminadas deben borrarse de:

```text
postman/pf-ecosystem.postman_collection.json
```

### Impacto sobre documentación

Se deben actualizar en el mismo cambio:

- `modules/pf-payroll/docs/api.md`
- `modules/pf-payroll/docs/payroll-workflow.md`
- cualquier referencia de endpoints en propuestas/investigaciones
- tests HTTP que prueben las rutas eliminadas

La documentación de los comandos CLI de `assign-plans`, `compute-contributions` y
`compute-tax` debe permanecer, porque esas interfaces siguen siendo válidas.

### Impacto sobre tests

Los tests de use cases y CLI de las tres operaciones conservadas internamente deben
permanecer.

Se deben eliminar o adaptar:

- tests de rutas HTTP para `assign-plans`;
- tests de rutas HTTP para `compute-contributions`;
- tests de rutas HTTP para `compute-tax`;
- tests de ruta HTTP para `deflate`;
- overrides FastAPI usados exclusivamente por esas rutas;
- dependencias HTTP que ya no tengan consumidores.

Los tests del use case `DeflateAmounts` solo deben conservarse si se decide mantener el
use case para una futura interfaz no HTTP. Si no existe tal uso, deben eliminarse junto
con el use case.

## 9. Secuencia de implementación recomendada

1. Eliminar las cuatro requests de `Periods / Operations` en Postman.
2. Eliminar las cuatro route handlers de `routes/payroll.py`.
3. Eliminar de `routes/payroll.py` los imports, DTOs de request/response y dependencias
   que queden sin uso por esas rutas.
4. Mantener los use cases `AssignPlans`, `ComputeContributions` y `ComputeIncomeTax`,
   junto con sus comandos CLI y uso interno en importación.
5. Eliminar `DeflateAmounts` completo si el análisis confirma que no existe otro uso
   dentro del ecosistema.
6. Actualizar `docs/api.md` para que el inventario HTTP refleje solamente las rutas
   realmente expuestas.
7. Actualizar `docs/payroll-workflow.md`: el workflow HTTP quedará documentado como
   `import → review`; las operaciones CLI de cálculo se documentarán por separado como
   herramientas internas/administrativas.
8. Ejecutar tests, mypy, ruff, coverage y jscpd.
9. Verificar el OpenAPI generado y confirmar que las cuatro rutas ya no aparecen.

## 10. Decisión final

### Eliminar

- `POST /payroll/{period_id}/assign-plans`
- `POST /payroll/{period_id}/compute-contributions`
- `POST /payroll/{period_id}/compute-tax`
- `POST /payroll/{period_id}/deflate`

### Mantener

- `POST /payroll/{period_id}/review`

La eliminación se limita inicialmente a la superficie HTTP para no romper las
capacidades internas y CLI que sí tienen uso dentro de `pf-payroll`. `deflate` es la
única excepción potencial: al no tener consumidores, tráfico exitoso, CLI ni uso
automático conocido, puede eliminarse completamente junto con su use case.

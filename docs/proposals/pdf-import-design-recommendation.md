## Resumen ejecutivo

Es factible y de **bajo riesgo** construir el flujo de import por PDF reusando el 100% del
cuadre de cuentas que ya existe (`from_rows()` nuevo + `ProcessImportedPayrollPeriods`
sin tocar). Recomendación en una línea: **plantilla como método de extracción para el
MVP** (barato, ya hay un caso real para construirla), **rutas nuevas** en vez de
sobrecargar `/payroll/import`, y **nada de conceptos catch-all** en `pf-db` — los gaps
reales que encontré se resuelven con plantillas + rechazo explícito en `commit`.

## 0. Hallazgo adicional: semántica del período (fuera del alcance del import de PDF)

Al comparar el período que declara el PDF ("Agosto") contra la base de datos, encontré
una inconsistencia real de convención, **independiente del feature de import por PDF**,
que el usuario decidió corregir:

- **Convención actual en los datos históricos:** `period_year`/`period_month` representa
  "el mes en el que voy a poder usar ese dinero" — un pago que cae a fin de mes se
  registraba bajo el período del mes SIGUIENTE.
- **Convención nueva acordada:** `period_year`/`period_month` representa **el mes
  trabajado**, tal cual lo declara el documento oficial (el PDF de agosto es agosto,
  punto). Es más simple, es la que ya usa el propio PDF, y es la que el modelo de datos
  ya asume por defecto para empleadores nuevos (`payment_month_offset=0` al crear un
  `EmployerModel` en el import).

1. **Código:** el importador (`payroll_repository_imports.py`) hoy toma `period_year`/
   `period_month`/`payment_date` de la fila tal cual vienen, sin cruzarlos contra la
   configuración del empleador (`payment_date_rule` / `payment_month_offset`). Falta una
   validación explícita que rechace (`PayrollValidationError`) un import donde el mes de
   `payment_date` no coincide con el que el `payment_month_offset` del empleador implica
   para ese período — así la convención vieja no puede volver a colarse en silencio.
2. **Datos:** los períodos históricos ya importados bajo la convención vieja para el
   empleador afectado necesitan recalcularse: `period_year`/`period_month` nuevo =
   año/mes de `payment_date` (dado que ese empleador usa `payment_month_offset=0`). Esto
   incluye refrescar la vista materializada `PAY_MV_SUMARY` después del `UPDATE`.

**Alcance de la corrección de datos:** no es solo el período de agosto que motivó esta
conversación — la revisión mostró que **todo** el historial de ese empleador sigue la
misma convención vieja de forma consistente, así que el fix de datos es "todo o nada"
para ese empleador, no un parche puntual de un solo período.

**Importante — alcance de ambiente:** cualquier corrección de datos debe aplicarse
primero en la base local de desarrollo, y **por separado, de forma explícita y
revisada**, contra la base real (Neon) cuando el usuario lo autorice — no hay hoy un
mecanismo de "push" de local hacia Neon, así que ese segundo paso requiere su propio
script SQL revisado a mano, no una copia automática.

## 1. Análisis del PDF adjunto

Analicé `secrets/Liquidación_202608.PDF` (Corporative Chile S.A., liquidación de agosto).
Nombres/RUT/montos reales fueron redactados de este documento — solo etiquetas y
estructura importan para el diseño.

### Campos que NO son `concept_code` (metadata de período/documento)

| Campo en el PDF | Dónde encaja en el dominio actual |
| --- | --- |
| Empleador (nombre) | `ImportPayrollRowDTO.employer` |
| RUT / nombre del trabajador | **No existe campo para esto en `ImportPayrollRowDTO` — y así debe seguir.** El dominio actual ya evita persistir PII del trabajador; el extractor de PDF debe leerlo solo para eventual validación cruzada (ej. confirmar que el PDF corresponde al empleado esperado) pero **nunca** incluirlo en el JSON de preview ni en lo persistido. |
| Mes / Año del período | `period_year`, `period_month` |
| Días trabajados | `worked_days` |
| Lugar de trabajo, sección, antigüedad laboral | Sin campo hoy — quedan fuera de alcance, no se extraen |
| Vía de pago, número de cuenta, banco | Sin campo hoy y sin caso de uso — **no extraer** |
| Totales (haberes / descuentos / líquido a pagar) | `declared_net_pay_clp` (el líquido a pagar declarado); el resto son subtotales derivados, no se persisten como ítems propios |

**Hallazgo sobre robustez de extracción:** el bloque resumen del encabezado (`SUELDO
BASE / HORAS EXTRAS / TOTAL IMPONIBLE / LEYES SOCIALES / AFECTO A IMPUESTO / IMPUESTO
UNICO`) tiene 6 columnas pero la fila de valores trae menos números cuando alguno es
cero — la extracción de texto plano corre el riesgo de desalinear columna con valor.
Esto es evidencia concreta (no hipotética) de por qué una extracción por
coordenadas/anclas o un LLM con schema forzado son preferibles a un simple
texto-a-texto: necesitan anclar cada valor a su etiqueta, no a su posición en una
secuencia.

### Mapeo de ítems detallados a `concept_code`

| Etiqueta en el PDF | Tipo | `concept_code` propuesto | Confianza | Nota |
| --- | --- | --- | --- | --- |
| SUELDO | haber | `SALARY_BASE` | Alta | Coincide con "SUELDO BASE" del encabezado |
| GRATIFICACION LEGAL | haber | `LEGAL_GRATUITY` | Alta | Nombre calza exacto |
| ASIGNACIÓN TRAB. HIBRIDO | haber | `TELEWORK_REFUND` | Media | "Asignación trabajo híbrido" ≈ reembolso teletrabajo; no imponible, calza con `is_taxable=FALSE` |
| APORTE SEGURO DE SALUD | haber | `HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION` | Media-Alta | "Aporte" = contribución del empleador |
| IMPUESTO | descuento | `INCOME_TAX` | Alta | Coincide con "IMPUESTO UNICO" del encabezado |
| COT. SEG. CES. AFP | descuento | `UNEMPLOYMENT_INSURANCE` | Alta | "Cotización Seguro de Cesantía" |
| ESENCIAL LEGAL | descuento | `HEALTH_BASE` | Media | Nombre de plan Isapre + "Legal" = tramo obligatorio (7%) |
| **COMISIÓN AFP** | descuento | `HEALTH_ADDITIONAL_UF` | Alta (confirmado) | Corrección del usuario: se bucketea junto al cargo adicional de salud |
| FONDO RETIRO AFP | descuento | `PENSION_BASE` (tentativo) | Media | Monto compatible con el 10% obligatorio, pero la etiqueta no lo dice explícito — requiere confirmación humana en la plantilla |
| ESENCIAL ADICIONAL | descuento | `HEALTH_ADDITIONAL_UF` | Media | Simetría con "Esencial Legal" / `HEALTH_BASE` |
| **SEGURO DENTAL** | descuento | `HEALTH_INSURANCE` | Alta (confirmado) | Corrección del usuario: se consolida junto a los otros dos seguros |
| SEGURO DE SALUD | descuento | `HEALTH_INSURANCE` | Alta | Nombre calza exacto |
| **SEGURO CATASTROFICO** | descuento | `HEALTH_INSURANCE` | Alta (confirmado) | Corrección del usuario: se consolida junto a los otros dos seguros |

**Corrección importante sobre mi análisis original:** había asumido que un `concept_code`
no podía repetirse dentro del mismo período (por eso marqué dental/catastrófico como
"sin match", para no "chocar" con `HEALTH_INSURANCE` ya usado por Seguro de Salud). Esa
asunción era **incorrecta** — no hay ninguna restricción de unicidad entre `PAY_ITEM` y
`PAY_CONCEPT` (la FK `concept_id` es simple, sin `UNIQUE` por período), así que **varios
ítems de una misma liquidación pueden compartir el mismo `concept_code`** sin problema.
Con esta corrección, **el PDF analizado ya no tiene gaps reales sin resolver** — los 3
casos que había marcado como "sin match" ahora están cubiertos.

## 2. Diseño de los dos endpoints

### Endpoint 1 — `POST /payroll/import/pdf-preview`

- Request: `multipart/form-data`, mismo patrón que `POST /payroll/import` (`UploadFile`).
- Response (`PdfImportPreviewResponse`): `employer`, `period_year`, `period_month`,
  `worked_days`, `declared_net_pay_clp`, y `rows: list[PdfImportPreviewRowDTO]` donde
  cada fila trae `raw_label`, `extracted_amount_clp`, `kind` (`income`/`discount`
  inferido), `concept_code: str | None`, `confidence: float`.
- **No inyecta `PayrollRepository` ni `ProcessImportedPayrollPeriods`** — es una función
  pura `bytes -> DTO`, sin I/O a base de datos. Nunca lanza 500 por PDF no reconocido:
  en el peor caso devuelve filas con `concept_code=null` y `confidence=0.0`.

### Endpoint 2 — `POST /payroll/import/rows`

- Request: JSON `{ "mode": "validate" | "commit", "rows": list[ImportPayrollRowDTO] }`.
- Nuevo método hermano en el use case: `ImportPayroll.from_rows(rows)` — llama directo a
  `self._repository.import_rows(rows)`, sin pasar por ningún `PayrollImporter` (idéntico
  a `from_bytes()` menos el paso de parseo).
- Delega 100% en `from_rows()` + `ProcessImportedPayrollPeriods.execute()` — la misma
  secuencia exacta que ya usa `/payroll/import` hoy.
- Mecanismo de transacción: envolver ambos use cases en una transacción explícita de
  `AsyncSession`; `commit` hace `session.commit()`, `validate` hace `session.rollback()`
  al final, devolviendo el mismo resultado en ambos casos (warnings, diffs, totales).

### ¿Reusar `/payroll/import` o rutas nuevas?

**Recomiendo rutas nuevas**, no sobrecargar `/payroll/import` con un content-type
condicional (multipart vs JSON) más un parámetro `mode`. Cada endpoint mantiene una sola
responsabilidad, un solo schema de request/response en OpenAPI, y `/payroll/import`
(CSV/XLSX) queda completamente intacto — cero riesgo de romper el flujo existente.

## 3. Manejo de conceptos sin match

**Nota tras la corrección de la sección 1:** el PDF analizado ya no tiene gaps reales
(los 3 casos que parecían sin match eran en realidad conceptos válidos, una vez
corregido el supuesto de que un `concept_code` no podía repetirse dentro del mismo
período). Igual dejo esta sección — el mecanismo sigue siendo necesario para el próximo
empleador/formato que traiga un concepto genuinamente nuevo.

**Requisito confirmado — varios ítems, mismo concepto:** el extractor y el endpoint 2
deben soportar que más de un ítem de la liquidación mapee al mismo `concept_code` (ej.
Seguro de Salud + Seguro Dental + Seguro Catastrófico → los tres bajo `HEALTH_INSURANCE`
como filas separadas). Esto no requiere ningún cambio de esquema: `PAY_ITEM.concept_id`
es una FK simple sin restricción `UNIQUE` por período, así que el modelo de datos ya lo
soporta hoy — el único cuidado es que la plantilla (sección 5) pueda declarar un mismo
`concept_code` para varios `pdf_label_pattern` distintos, en vez de asumir una relación
1 a 1 etiqueta↔concepto.

| Opción | Pros | Contras | Impacto en `validate`/`commit` |
| --- | --- | --- | --- |
| (a) Catch-all seedeado en pf-db (`OTHER_INCOME`/`OTHER_DISCOUNT`) | Garantiza que el import nunca se bloquee | Destruye precisión de auditoría (no se puede cuadrar contra pf-rates un balde sin diferenciar); coordinación cross-repo obligatoria; riesgo de "adivinar" silenciosamente si se autoasigna sin humano | N/A — evita el problema en vez de resolverlo |
| (b) Mapeo manual por plantilla de empleador, con fallback "no resuelto" | Preciso; respeta el catálogo cerrado a propósito; crecimiento deliberado por humano | Costo de setup inicial por cada empleador/formato nuevo | Ninguno directo — es la capa de extracción, no de persistencia |
| (c) Fila sin `concept_code` en el preview; endpoint 2 la rechaza si llega sin resolver | Cero cambios de esquema; fuerza decisión humana explícita; compone con (b) | No persiste el monto hasta que alguien lo resuelva | `commit` **debe fallar** (`PayrollValidationError`) si queda algún `concept_code=null`; `validate` **no falla** — devuelve warning con el detalle para que el usuario decida antes de confirmar |

**Recomendación: (b) + (c) juntas, nunca (a).** Si con el tiempo un mismo concepto sin
match (ej. seguro dental) aparece repetido entre varios empleadores, ahí sí se justifica
agregar un concepto **con nombre propio** vía migración en pf-db — nunca un catch-all
genérico.

## 4. Opciones de extracción (solo endpoint 1)

| Método | Precisión | Tolerancia a cambio de formato | Costo mensual estimado | Complejidad | Testing sin datos reales |
| --- | --- | --- | --- | --- | --- |
| Plantilla (anclas/regex) | Alta para el mismo formato; ~0% si cambia sin aviso | Baja | **$0** — solo CPU local | Media (una plantilla por empleador+versión) | Trivial: fixtures sintéticos con el mismo layout |
| LLM con schema JSON forzado (AI Innovation Lab) | Alta, robusta a cambios de orden/formato | Alta | Variable por documento — **cotizar en AI Innovation Lab antes de comprometerse**, no inventar cifra acá | Baja-Media (prompt + validación de schema) | Mockear el proveedor con respuestas JSON grabadas, sin llamadas reales en CI |
| OCR (PDFs escaneados) | Media, depende de calidad del escaneo | Baja-Media | $0 (Tesseract) vs pago por página (Document AI/Cloud Vision) | Alta (preprocesamiento de imagen + extracción posterior) | Complejo — requiere fixtures de imagen |
| Híbrido (plantilla primero, LLM como fallback) | Alta en el caso común y en el raro | Alta | Mínimo — LLM solo se invoca cuando la plantilla falla | Alta (dos pipelines + lógica de decisión) | Combina los dos anteriores por separado |

**Recomendación:** empezar el MVP **solo con plantilla** (ya hay un caso real de Corporative
Chile para construirla) y agregar el fallback LLM recién cuando aparezca un segundo
empleador/formato real. Partir con LLM desde el día 1 sin haber intentado plantilla
sería sobre-ingeniería cara — va contra la regla del ecosistema de que la opción más
barata gana salvo justificación explícita.

## 5. Sistema de plantillas versionables — flujo concreto

### Formato y ubicación

JSON plano (sin agregar dependencia de parsing nueva), un archivo por versión, en
`pf-payroll/infrastructure/pdf_import/templates/<employer_slug>/v<N>.json`. Nada de esto
vive en base de datos a propósito (YAGNI): son archivos versionados en git, así el
historial de cada cambio de mapeo queda en el log de commits del repo, no en una tabla
mutable sin auditoría.

Ejemplo real (con las correcciones de la sección 1 ya aplicadas — nótese cómo **un solo
pattern puede matchear varias etiquetas del PDF y mapear al mismo concepto**, cubriendo
el requisito de varios ítems → un concepto):

```json
{
  "template_id": "corporative-chile-v1",
  "employer_match": { "name_pattern": "(?i)corporative chile" },
  "fields": [
    { "pdf_label_pattern": "(?i)^SUELDO$", "concept_code": "SALARY_BASE", "kind": "income" },
    { "pdf_label_pattern": "(?i)GRATIFICACION LEGAL", "concept_code": "LEGAL_GRATUITY", "kind": "income" },
    { "pdf_label_pattern": "(?i)SEGURO (DE SALUD|DENTAL|CATASTROFICO)", "concept_code": "HEALTH_INSURANCE", "kind": "discount" },
    { "pdf_label_pattern": "(?i)COMISI[OÓ]N AFP", "concept_code": "HEALTH_ADDITIONAL_UF", "kind": "discount" }
  ]
}
```

### Flujo paso a paso

1. **Arranque en frío (empleador/formato nuevo, cero plantillas):** llega un PDF que no
   matchea ninguna plantilla existente. El endpoint 1 igual responde 200 — nunca 500 —
   con todas las filas en `concept_code: null`, `confidence: 0.0`. No hay magia acá: un
   humano abre el PDF al lado del JSON de preview y arma la primera plantilla a mano.
2. **Autodetección en imports posteriores:** al llegar un nuevo PDF, el extractor prueba
   cada plantilla contra el texto extraído y calcula un score = cantidad de
   `pdf_label_pattern` que matchean al menos una línea del detalle. Se usa la plantilla
   con mayor score si supera un umbral mínimo (ej. ≥ 3 matches); si ninguna lo supera,
   se cae al comportamiento del punto 1 (preview vacío, no un error).
3. **Aplicar la plantilla ganadora:** cada línea del detalle de haberes/descuentos del
   PDF se compara, en orden, contra los `pdf_label_pattern` de la plantilla elegida. El
   primero que matchea define `concept_code` y `kind`; si ninguno matchea, esa fila
   puntual queda `concept_code: null` (aunque el resto del PDF sí haya reconocido una
   plantilla) — esto es lo que dispara la sección 3 (fila sin resolver).
4. **Ajustar sin programar:** un comando CLI de apoyo (`payroll template test <pdf>
   --employer corporative-chile`) corre la extracción de texto + intenta las plantillas
   existentes y lista qué filas del PDF quedaron sin match. Un humano edita el JSON a
   mano (agrega o ajusta un `pdf_label_pattern`) y vuelve a correr el comando hasta que
   reporte 0 filas sin resolver.
5. **Nueva versión (el mismo empleador rediseña su PDF):** la señal es que, de golpe, el
   CLI reporta muchas filas sin match aunque existía una plantilla que antes funcionaba
   bien. Ahí se crea `v2.json` (copiando `v1.json` como base) y se ajusta — **nunca se
   edita `v1.json` in-place** una vez que ya se usó, para que reprocesar un PDF viejo dé
   siempre el mismo resultado.
6. **Quién mantiene esto:** es un proceso deliberadamente manual y humano (coincide con
   la recomendación (b) de la sección 3: crecimiento del mapeo por decisión explícita,
   nunca automático/silencioso). No hay ningún job ni proceso que genere o edite
   plantillas solo — sí puede haber, a futuro, un fallback a LLM (sección 4) para el
   caso en que ninguna plantilla matchea, pero eso es una capa aparte, no reemplaza este
   flujo manual de curaduría por empleador.

## 6. Testing del modo validate/commit

- **Sin residuos:** usar `testcontainers[postgres]` (ya es dependencia dev existente).
  Correr `mode=validate`, contar filas de `PAY_ITEM` antes/después (debe ser igual),
  correr `mode=commit` con las mismas filas, contar de nuevo (debe subir).
- **Mock de pf-rates:** `MarketDataRepository` ya es un `Protocol` — reusar el mismo fake
  que hoy hace posible el 100% de cobertura de `ProcessImportedPayrollPeriods` (DRY, no
  crear uno nuevo).

## 7. Plan de acción

0. **Etapa 0 (previa e independiente del import de PDF):** corregir la semántica del
   período según el hallazgo de la sección 0 — (a) agregar la validación de
   `payment_date` vs. `payment_month_offset` en el importador, y (b) recalcular
   `period_year`/`period_month` de los períodos históricos afectados a partir de
   `payment_date`, refrescando `PAY_MV_SUMARY`. Primero en local, luego (aparte, con
   autorización explícita) en Neon. **Sin implementar todavía** — queda pendiente de luz
   verde del usuario, tratada como su propio pedazo de trabajo, no atado al roadmap del
   import de PDF.
1. **Etapa 1 (MVP):** Endpoint 1 (preview) con extracción por plantilla, una plantilla
   real (Corporative Chile, basada en este PDF). Entregable independiente y testeable.
2. **Etapa 2:** Endpoint 2 solo en modo `commit` (`from_rows()` + `ProcessImportedPayrollPeriods`,
   sin dry-run todavía) — valida el mecanismo de reuso antes de sumar la complejidad de
   transacciones.
3. **Etapa 3:** Modo `validate` (rollback transaccional).

**Riesgos:** coordinación cross-repo con pf-db solo aplicaría si se eligiera la opción
(a) de la sección 3 — como la recomendación explícita es NO tomarla, el MVP no requiere
ninguna migración en pf-db. El único efecto secundario ya documentado en el brief
persiste: `ProcessImportedPayrollPeriods` puede cachear market data en la base de
pf-rates incluso en modo `validate`.

## Ejemplos de JSON

### Preview (endpoint 1) — nombres genéricos, sin datos reales

```json
{
  "employer": "ACME_CL",
  "period_year": 2026,
  "period_month": 8,
  "worked_days": 30,
  "declared_net_pay_clp": "1500000.00",
  "rows": [
    {
      "raw_label": "SUELDO",
      "extracted_amount_clp": "1200000.00",
      "kind": "income",
      "concept_code": "SALARY_BASE",
      "confidence": 0.98
    },
    {
      "raw_label": "SEGURO DENTAL",
      "extracted_amount_clp": "8000.00",
      "kind": "discount",
      "concept_code": null,
      "confidence": 0.0
    }
  ]
}
```

### Confirmación (endpoint 2)

```json
{
  "mode": "validate",
  "rows": [
    {
      "employer": "ACME_CL",
      "period_year": 2026,
      "period_month": 8,
      "payment_date": "2026-08-31",
      "status": "actual",
      "employment_contract_kind": "indefinite",
      "concept_code": "SALARY_BASE",
      "amount_clp": "1200000.00",
      "worked_days": 30,
      "declared_net_pay_clp": "1500000.00"
    }
  ]
}
```

## Recomendación final

0. **Semántica del período:** adoptar "el período representa el mes trabajado" (como el
   PDF), no "el mes en que se puede gastar la plata". Corrección de código (validación
   en el import) + corrección de datos históricos, ambas pendientes de aprobación
   explícita — ver Etapa 0 del plan de acción.
1. Extracción: **plantilla primero**, LLM (vía AI Innovation Lab) solo como fallback en
   una segunda etapa.
2. Conceptos sin match: **plantillas + rechazo explícito en `commit`**, nunca catch-all
   en pf-db. El PDF analizado no dejó gaps reales una vez soportado que varios ítems
   compartan un mismo `concept_code`, pero el mecanismo sigue vigente para el próximo
   formato/empleador nuevo.
3. **Rutas nuevas** (`/payroll/import/pdf-preview`, `/payroll/import/rows`) en vez de
   sobrecargar `/payroll/import`.
4. Reusar el 100% de `ProcessImportedPayrollPeriods` sin tocarlo — el diseño ya estaba
   preparado para esto.
5. Plan en 4 etapas (0 a 3), con el preview como MVP entregable de forma independiente
   y la Etapa 0 tratada como trabajo aparte, no bloqueante del resto.

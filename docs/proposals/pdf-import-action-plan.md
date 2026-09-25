# PDF import — plan de acción (documento vivo)

> **Regla de este documento:** a diferencia de `pdf-import-design-brief.md` y
> `pdf-import-design-recommendation.md` (que son el pedido y el análisis originales, y
> quedan congelados como fueron escritos), **este archivo se actualiza en cada sesión de
> trabajo** sobre el feature. Cualquier avance, hallazgo nuevo, decisión tomada, o cambio
> de alcance va acá, con fecha, antes de dar por cerrada una etapa. Si encontrás este
> documento desactualizado respecto al código, actualizalo vos mismo antes de seguir.

## Estado general

| Etapa | Descripción | Estado |
| --- | --- | --- |
| 0 | Semántica de período (código + datos históricos) | **Completa** — código, datos locales, Neon (corregido por el usuario) y el CSV fuente ya alineados |
| 1 | Endpoint 1 — preview PDF (MVP) | **Completa** — extractor por plantilla, template real de WALMART-CHILE, ruta `POST /payroll/import/pdf-preview` |
| 2 | Endpoint 2 — confirmar, modo `commit` | **Completa** — `ImportPayroll.from_rows()`, ruta `POST /payroll/import/rows` |
| 3 | Endpoint 2 — modo `validate` (rollback) | **Completa** — `TransactionalSessionScope` con SAVEPOINT real |

## Etapa 0 — Semántica del período

### Qué se hizo (2026-09-25)

1. **Código:** `SqlAlchemyPayrollImportRepository._validate_payment_month_matches_period()`
   en `infrastructure/db/repositories/payroll_repository_imports.py`. Se llama en
   `import_rows()` justo después de resolver/crear el `EmployerModel`, antes de tocar
   `PAY_PERIOD`. Compara el mes/año de `payment_date` contra
   `add_months(date(period_year, period_month, 1), employer.payment_month_offset)` y
   lanza `PayrollValidationError` si no calzan. Con `payment_month_offset=0` (el default
   para todo empleador nuevo), esto exige que `payment_date` caiga en el mismo mes que
   `period_month` — la convención nueva, tal cual la declara el PDF.
   - Test agregado: `test_sa_payroll_repository_rejects_payment_date_period_mismatch`
     en `tests/unit/infrastructure/test_payroll_repository.py` (cubre la rama del
     `raise`, necesario para mantener `--cov-fail-under=100`).
   - Cobertura verificada: 100% (286 tests, `pytest --cov=payroll --cov-report=term-missing`).
   - Lint (`make lint`) y typecheck (`make typecheck`) verificados, sin hallazgos.
   - **No rompió ningún test existente** — todos los fixtures/tests actuales ya asumían
     `payment_date` y `period_month` en el mismo mes (offset 0), por lo que la
     validación nueva pasó en verde sin tocar tests preexistentes de import.

2. **Datos (solo entorno local):** se identificó en la DB local que **`WALMART-CHILE`**
   (`PAY_EMPLOYER.id = 9` en local, `payment_month_offset = 0`) es el único empleador
   con períodos importados (22 filas, dic-2024 a sep-2026), y **el 100%** seguía la
   convención vieja (`period_month` = mes de `payment_date` + 1). Los otros dos
   empleadores locales (`DALT-CONSULTORES`, `CLINICA-ALEMANA`) no tienen períodos
   cargados, no había nada que corregir ahí.

   Se usó un script SQL puntual (idempotente, sin PII — solo `employer_id`/fechas,
   nunca RUT/nombre de trabajador porque `PAY_PERIOD` no los almacena) para aplicar la
   corrección. **Por indicación explícita del usuario, el archivo se eliminó del repo**
   una vez que local, Neon y el CSV fuente quedaron alineados — ya cumplió su función y
   no se conserva versionado (si hiciera falta reconstruirlo, el SQL era trivial: `UPDATE
   "PAY_PERIOD" SET period_year = EXTRACT(YEAR FROM payment_date), period_month =
   EXTRACT(MONTH FROM payment_date) WHERE employer_id = <id de WALMART-CHILE>;` seguido
   de `REFRESH MATERIALIZED VIEW "PAY_MV_SUMARY"`).

   Aplicado y verificado en local:
   ```
   UPDATE 22   -- las 22 filas de WALMART-CHILE
   REFRESH MATERIALIZED VIEW "PAY_MV_SUMARY"
   ```
   Verificación post-fix: `period_year`/`period_month` de cada fila ahora coincide
   exactamente con el año/mes de su `payment_date` (ej. `payment_date=2024-11-28` ahora
   vive en el período `2024-11`, ya no en `2024-12`).

### Neon (2026-09-25)

El usuario corrigió Neon directamente (fuera de mi alcance, tal como quedó acordado —
yo no toqué producción). Etapa 0 queda cerrada del lado de base de datos.

### Fuente de datos corregida (2026-09-25)

`secrets/payroll-input.csv` (el CSV real usado para importar el historial de
WALMART-CHILE, fuera de git por PII/datos financieros reales) tenía la misma
convención vieja en sus 22 filas: `period_year`/`period_month` = mes de `payment_date`
+ 1. Se corrigió recalculando `period_year`/`period_month` directamente a partir de
`payment_date` con un script Python puntual (no versionado, se corrió una sola vez —
esto es un archivo de datos gitignoreado, no código de producción, así que no ameritó
un script en `scripts/data-fixes/` como el de la DB). Verificado: las 22 filas quedaron
con `period_year`/`period_month` igual al año/mes de su propio `payment_date`,
consistente con la corrección ya aplicada en local y en Neon. Si este CSV se reimporta
hoy, pasa limpio por la validación nueva de la sección "Qué se hizo" sin disparar
`PayrollValidationError`.

## Etapa 1 — Endpoint 1 (preview PDF)

**Completa (2026-09-25).** Sin persistencia: ni `PayrollRepository` ni
`ProcessImportedPayrollPeriods` están en el grafo de dependencias de este endpoint, por
construcción (`PreviewPdfImport.__init__` solo recibe el puerto `PdfPayrollExtractor`).

### Qué se construyó

- **DTOs** (`application/dto.py`): `PdfImportPreviewRowDTO` (raw_label,
  extracted_amount_clp, kind, concept_code opcional, confidence) y
  `PdfImportPreviewDTO` (employer/period/worked_days/declared_net_pay_clp, todos
  opcionales, + template_id + rows). Todo opcional a propósito: el contrato es "nunca
  falla ruidosamente", en el peor caso se devuelve un preview casi vacío con 200 OK.
- **Puerto** (`application/ports/pdf_extractors.py`): `PdfPayrollExtractor.extract_preview()`.
- **Use case** (`application/use_cases/preview_pdf_import.py`): `PreviewPdfImport` —
  deliberadamente sin repositorio, solo delega al extractor.
- **Infraestructura nueva** (`infrastructure/pdf_import/`):
  - `text_extraction.py`: helpers genéricos (agnósticos de empleador) para leer texto
    de un PDF con `pypdf` (`extraction_mode="layout"`), parsear header (mes/año en
    español, días trabajados, líquido a pagar) y separar el detalle en líneas
    `(label, monto)`, con un heurístico posicional (columna HABERES vs DESCUENTOS) como
    señal de respaldo cuando ninguna plantilla resuelve una línea.
  - `templates.py`: carga y matching de plantillas JSON versionadas. Selección en dos
    pasos: filtro por `employer_match.name_pattern` contra el texto completo, luego
    score = cantidad de `fields` cuyo patrón matchea al menos un label del detalle;
    umbral mínimo `MIN_TEMPLATE_MATCH_SCORE = 3` (si no se alcanza, no se asume nada).
  - `extractor.py`: `TemplatePdfPayrollExtractor`, la implementación del puerto.
    Envuelve todo en un `try/except Exception` — un PDF corrupto, escaneado sin capa de
    texto, o cualquier fallo interno inesperado degrada a un preview vacío en vez de
    romper el endpoint.
  - `templates/walmart-chile/v1.json`: **plantilla real**, construida y validada contra
    la liquidación real del usuario (`secrets/Liquidación_202608.PDF`, nunca commiteada
    — solo se usó localmente para diseñar/probar la plantilla). Mapea los 13 conceptos
    reales de WALMART-CHILE a sus `concept_code` de `PAY_CONCEPT`, con la confianza que
    ya se había acordado en `pdf-import-design-recommendation.md` (Alta=0.9,
    Media-Alta=0.75, Media=0.6). Verificado extremo a extremo contra el PDF real:
    empleador, período (2026-8, ya con la convención nueva), días trabajados (30) y
    líquido a pagar (3.133.182) — los 13 conceptos resuelven a un `concept_code`, cero
    filas sin resolver.
- **Ruta** (`interfaces/api/routes/payroll.py`): `POST /payroll/import/pdf-preview`,
  wireada en `interfaces/api/dependencies.py::get_preview_pdf_import_use_case` (sin
  `Depends` de repositorio, a propósito).

### Decisiones/ajustes durante la implementación

- El heurístico de columna (HABERES vs DESCUENTOS) para filas no resueltas por ninguna
  plantilla se calcula **siempre** sobre el texto del documento (no solo cuando no hay
  plantilla), para que también sirva de respaldo en una fila puntual sin match dentro
  de un documento cuya plantilla sí matcheó en general.
- Un token corto (3-6 caracteres) inmediatamente antes del monto solo se trata como
  "código de concepto" (y se recorta del label) si contiene al menos un dígito — todos
  los códigos reales observados mezclan letras y números (`1E89`, `/370`, `3C30`).
  Sin esto, una palabra corta en mayúsculas al final de un label sin código real (ej.
  "LABEL") se recortaba por error.
- Cobertura: 100% en los 4 archivos nuevos de `infrastructure/pdf_import/` + use case +
  wiring de dependencias/ruta (337 tests totales, suite completa). Dos líneas
  defensivas genuinamente inalcanzables (dado que las regex que las preceden ya
  garantizan la condición) quedaron marcadas `# pragma: no cover` en vez de forzar un
  test artificial.

### Pendiente / fuera de alcance de esta etapa

- Solo hay plantilla para WALMART-CHILE. Cualquier otro empleador/formato hoy cae en
  "sin plantilla" (preview con filas sin resolver, pero header parseado igual).
- No hay OCR ni LLM — PDFs escaneados sin capa de texto seleccionable devuelven un
  preview vacío. Fuera de alcance del MVP, tal como está en
  `pdf-import-design-recommendation.md`.

## Etapa 2 — Endpoint 2, modo `commit`

**Completa (2026-09-25).** Reusa el 100% del pipeline existente — cero cambios en
`SqlAlchemyPayrollImportRepository.import_rows()`, que ya era agnóstico de la fuente de
las filas.

### Qué se construyó

- **Use case** (`application/use_cases/import_payroll.py`): `ImportPayroll.from_rows()`,
  método hermano de `from_bytes()`. Salta el paso de parseo (`PayrollImporter`) y llama
  directo a `self._repository.import_rows(rows)`. Rechaza (`PayrollValidationError`)
  una lista vacía, igual que `from_bytes()` rechaza un archivo sin filas.
- **Modelos de request** (`interfaces/api/routes/payroll.py`): `ImportPayrollRowRequest`
  (espejo de `ImportPayrollRowDTO`, sin los campos de solo-salida
  `expected_net_pay_clp`/`net_pay_difference_clp`) y `ImportPayrollRowsRequest`
  (`mode` + `rows`). `mode` es un `Literal["commit"]` a propósito — todavía no existe
  `"validate"`, así que mandar ese valor da 422 por schema, no por lógica de negocio
  escrita a mano (nada de branches muertos esperando la Etapa 3).
- **Ruta**: `POST /payroll/import/rows`, misma secuencia exacta que `/payroll/import`
  (`ImportPayroll.from_rows()` → `ProcessImportedPayrollPeriods.execute()`), reusando
  la respuesta `ImportPayrollResponse` ya existente.

### Cómo se resuelve "rechazar concept_code sin resolver" (sección 3 del diseño)

Sin código defensivo extra: `ImportPayrollRowRequest.concept_code` es `str` (no
`str | None`), así que un row con `concept_code: null` en el body nunca llega al
handler — FastAPI/pydantic lo rechazan con 422 antes. El tipo hace campamento donde
antes hubiera hecho falta un `if`.

### Testing

- `tests/unit/application/test_import_payroll.py`: 2 tests nuevos para `from_rows()`
  (delega bien a la fake repository / rechaza lista vacía).
- `tests/integration/api/test_payroll_import_rows.py` (nuevo archivo): happy path
  (modo por default y explícito), 422 en modo `validate`, 422 en `concept_code` nulo,
  400 en lista vacía (propagado desde el use case), 502 cuando
  `ProcessImportedPayrollPeriods` falla por una dependencia caída (mismo patrón que
  el test equivalente de `/payroll/import`).
- 345 tests totales, 100% cobertura (`--cov-fail-under=100`), lint/typecheck/vulture
  limpios.

### Pendiente / fuera de alcance de esta etapa

- No hay smoke test manual contra una base Postgres real corriendo (no había una
  instancia local levantada en esta sesión) — la confianza viene de que
  `import_rows()` no se tocó (ya estaba 100% cubierto por `/payroll/import`) y de que
  `from_rows()` es un passthrough trivial, verificado con fakes.
- El mecanismo de transacción real (`session.commit()` vs `session.rollback()`) queda
  para la Etapa 3 — como `import_rows()` ya hace *múltiples* `commit()` internos por
  período (no uno solo al final), un `validate` correcto necesita replantear el scope
  de la sesión, no solo agregar un `if mode == "commit"` al final.

## Etapa 3 — Endpoint 2, modo `validate`

**Completa (2026-09-25).** El hallazgo documentado al cerrar la Etapa 2 se confirmó
correcto: `import_rows()` (y varios pasos de `ProcessImportedPayrollPeriods`, ver
`_refresh_summary_view()`/`_reconcile_period_net_pay()` en
`payroll_repository_shared.py`, y `payroll_repository_commands.py`) hacen **varios**
`session.commit()` internos durante un solo request. Envolver la llamada final en un
`session.rollback()` no hubiera deshecho nada de eso — cada `commit()` interno ya
había hecho su propia transacción durable.

### Cómo se resolvió: SAVEPOINT real, no un flag manual

En vez de tocar los `session.commit()` existentes esparcidos por medio codebase (docenas
de métodos en `payroll_repository_commands.py`, `payroll_repository_shared.py`, y varios
use cases), se usa el soporte nativo de SQLAlchemy 2.0 para "unirse" una sesión a una
transacción externa con semántica de SAVEPOINT:
`AsyncSession(bind=connection, join_transaction_mode="create_savepoint")`. Con esto,
cada `session.commit()` que hace el código de aplicación **solo libera el SAVEPOINT
actual** (SQLAlchemy abre uno nuevo automáticamente) — la transacción real de la
conexión nunca se toca hasta que alguien llama explícitamente `resolve()`. Cero cambios
en el código existente que ya llamaba `session.commit()` libremente.

- **`interfaces/session.py`**: `TransactionalSessionScope` (envuelve `session` +
  `_transaction`, expone `resolve(mode)` que hace `transaction.commit()` o
  `transaction.rollback()`) y `open_transactional_session()` (abre la conexión, arranca
  la transacción real, construye la sesión con `join_transaction_mode="create_savepoint"`
  y `expire_on_commit=False` — igual que `SessionLocal` — y hace rollback defensivo en
  el `finally` si `resolve()` nunca se llegó a llamar).
- **`interfaces/api/dependencies.py`**: `get_transactional_session()` (dependencia
  FastAPI, cacheada por request) + 4 dependencias hermanas
  (`get_payroll_repository_for_rows_import`,
  `get_complementary_insurance_repository_for_rows_import`,
  `get_import_payroll_use_case_for_rows_import`,
  `get_process_imported_payroll_periods_use_case_for_rows_import`) que construyen los
  mismos use cases de la Etapa 2 pero atados a la sesión transaccional en vez de la
  sesión "plana" de siempre — necesarias porque FastAPI cachea dependencias por
  callable, no hay forma de "parametrizar" `get_session()` según el `mode` del body.
- **Ruta**: ahora un solo `try/except` alrededor de `from_rows()` +
  `ProcessImportedPayrollPeriods.execute()` (antes eran dos, como en `/payroll/import`)
  — divergencia deliberada: cualquier excepción fuerza `scope.resolve("validate")`
  **antes** de re-lanzar el error, sin importar qué `mode` había pedido el cliente. Un
  import a medio aplicar nunca puede quedar comiteado.
- `ImportPayrollRowsRequest.mode` pasó de `Literal["commit"]` a
  `Literal["commit", "validate"]`.

### Testing — el primer test contra Postgres real de todo pf-payroll

Toda la suite existente usa fakes (`FakeSession`, `FakeResultsQueueBase`, etc.) y eso
sigue siendo correcto para el 99% del dominio. Pero el mecanismo de SAVEPOINT hace una
afirmación sobre semántica **real** de transacciones que ningún fake puede verificar
honestamente: que varios `session.commit()` internos de verdad desaparecen con
`resolve("validate")`. Para eso:

- `tests/integration/infrastructure/test_transactional_session.py` (nuevo): usa
  `testcontainers[postgres]` (dependencia dev ya declarada, nunca antes usada en este
  repo) contra una tabla `probe` desechable, sin tocar el esquema de pf-db. Tres casos:
  `commit` persiste los dos `commit()` internos, `validate` los descarta a ambos, y un
  scope nunca resuelto (bug/early-return) hace rollback defensivo solo. Contenedor
  `postgres:16-alpine` reusado a nivel de módulo (ya estaba cacheado localmente); motor
  async fresco por test para evitar cruzar el connection pool de asyncpg entre distintos
  event loops de pytest-asyncio (`asyncio_mode = "strict"`, sin loop compartido).
  - Nota de entorno: Ryuk (el "reaper" de testcontainers) falla al arrancar en Rancher
    Desktop/macOS con un error de mount del socket de Docker — problema conocido, ya
    resuelto en este monorepo: `pf-common/make/common.mk` detecta el socket de Rancher y
    exporta `TESTCONTAINERS_RYUK_DISABLED=true` automáticamente para `make test`/
    `make test-cov`. No hizo falta tocar nada ahí, solo usarlo.
- `tests/unit/interfaces/test_api_dependencies.py`: 5 tests nuevos para las
  dependencias `_for_rows_import` + el ciclo de vida de `get_transactional_session()`
  (mismo patrón de `assert_get_session_lifecycle` ya existente, ahora también
  `assert_get_transactional_session_lifecycle` en `tests/helpers/db_fakes.py`).
- `tests/integration/api/test_payroll_import_rows.py`: reescrito con
  `FakeTransactionalSessionScope` (graba con qué `mode` se llamó `resolve()`). Casos:
  commit por default y explícito, `validate` (pipeline completo corre, pero
  `resolve("validate")`), 422 en `concept_code` nulo, 422 en `mode` desconocido, rollback
  forzado cuando `from_rows()` falla (aunque se pidió `commit`), rollback forzado cuando
  `ProcessImportedPayrollPeriods` falla por dependencia caída (502).
- 354 tests totales, 100% cobertura (`--cov-fail-under=100`), lint/typecheck/vulture
  limpios.

### Pendiente / fuera de alcance de esta etapa

- No hay un smoke test end-to-end contra el esquema real de pf-db (migraciones +
  seeds de `PAY_CONCEPT`/planes) ejecutando la ruta HTTP real sin fakes. La confianza
  viene de tres capas independientes ya cubiertas: el mecanismo SAVEPOINT en sí
  (Postgres real), la orquestación de la ruta (qué `resolve()` se llama y cuándo,
  fakes), y `import_rows()`/`ProcessImportedPayrollPeriods` sin cambios (ya cubiertos
  al 100% desde antes de este feature). Si se quiere ese smoke test completo algún día,
  ya queda toda la infraestructura de testcontainers lista para reusar.

## Historial de cambios

- **2026-09-25 (cont. 6)** — Fix post-push: el push de la Etapa 3 rompió CI (`gh run list`
  mostró el run en `failure`). Causa: dos tests de `test_payroll_import_rows.py`
  (los que esperaban 422 por body inválido) no tenían overrides de dependencias,
  asumiendo que FastAPI corta la resolución de `Depends()` antes de validar el body.
  **Eso es falso** — FastAPI resuelve todo el árbol de dependencias (incluyendo
  `get_transactional_session()`, que abre una conexión real vía `engine.connect()`)
  antes de mirar los errores de validación del body. Localmente los tests igual
  pasaban porque había un túnel SSH viejo escuchando en el puerto 5432 de la máquina
  apuntando a una base real; en el runner de GitHub Actions no hay nada ahí y explota
  con `OSError: Connect call failed`. Fix: los dos tests ahora también mockean
  `get_transactional_session` (como el resto), y se agregó `scope.resolved_with == []`
  como assertion explícita de que la dependencia transaccional real nunca se toca en
  esos casos. Verificado apuntando `PF_DATABASE_URL` a un puerto que de verdad no
  responde (simulando CI) antes de repushear — 354 tests, 100% cobertura.
  **Lección para próximas rutas con dependencias que hacen I/O eager:** nunca asumir
  que un test de "422 por body inválido" puede saltarse los overrides de dependencias
  solo porque el handler nunca las va a usar — FastAPI las instancia igual.
- **2026-09-25 (cont. 5)** — Etapa 3 completa: `TransactionalSessionScope` con
  `join_transaction_mode="create_savepoint"` de SQLAlchemy, ruta `POST
  /payroll/import/rows` ahora soporta `mode="validate"` de verdad (pipeline completo,
  cero escritura persistida). Primer test contra Postgres real de todo pf-payroll
  (`testcontainers[postgres]`). 354 tests, 100% cobertura, lint/typecheck/vulture
  limpios. Ver detalle en la sección de la Etapa 3 arriba.
- **2026-09-25 (cont. 4)** — Etapa 2 completa: `ImportPayroll.from_rows()`, ruta
  `POST /payroll/import/rows` (modo `commit` únicamente), reusando 100% del pipeline
  existente. 345 tests, 100% cobertura, lint/typecheck/vulture limpios. Ver detalle en
  la sección de la Etapa 2 arriba.
- **2026-09-25 (cont. 3)** — Etapa 1 completa: extractor por plantilla, plantilla real
  de WALMART-CHILE, endpoint `POST /payroll/import/pdf-preview` sin persistencia.
  337 tests, 100% cobertura, lint/typecheck/vulture limpios. Ver detalle en la sección
  de la Etapa 1 arriba.
- **2026-09-25 (cont. 2)** — A pedido explícito del usuario, se eliminó del repo
  `scripts/data-fixes/2026-09-fix-period-semantics-walmart-chile.sql` (ya no hacía
  falta: local, Neon y el CSV fuente ya estaban alineados). El SQL quedó documentado
  arriba por si hace falta reconstruirlo.
- **2026-09-25 (cont.)** — Usuario confirmó corrección manual de Neon. Corregido también
  `secrets/payroll-input.csv` (CSV fuente real del import de WALMART-CHILE, gitignoreado)
  con la misma recalculación `period_year/period_month = año/mes de payment_date`.
  **Etapa 0 cerrada por completo** (código + los tres lugares de datos: local, Neon,
  CSV fuente).
- **2026-09-25** — Arranque del plan de acción. Etapa 0 completada en código y en datos
  locales; Neon queda pendiente de autorización explícita. Creado este documento de
  seguimiento (separado de la propuesta original, que queda congelada).

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
| 1 | Endpoint 1 — preview PDF (MVP) | No iniciada |
| 2 | Endpoint 2 — confirmar, modo `commit` | No iniciada |
| 3 | Endpoint 2 — modo `validate` (rollback) | No iniciada |

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

No iniciada. Próximo paso cuando arranque: crear
`payroll/infrastructure/pdf_import/` (extractor por plantilla + carga de plantillas
JSON), el DTO de preview enriquecido, y la ruta
`POST /payroll/import/pdf-preview`. Ver sección 2 y 5 de
`pdf-import-design-recommendation.md` para el diseño ya acordado.

## Etapa 2 — Endpoint 2, modo `commit`

No iniciada. Depende de: `ImportPayroll.from_rows()` (nuevo método hermano de
`from_bytes()`) + ruta `POST /payroll/import/rows`.

## Etapa 3 — Endpoint 2, modo `validate`

No iniciada. Depende de Etapa 2. Mecanismo: misma secuencia, `session.rollback()` en vez
de `session.commit()` al final.

## Historial de cambios

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

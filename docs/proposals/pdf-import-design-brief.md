## Contexto

Quiero construir una funcionalidad para que **pf-payroll** pueda extraer datos de una
liquidación de sueldo a partir de su PDF, sin digitar los datos a mano. El alcance de
este trabajo es **exclusivamente backend**: dos endpoints y su contrato. Cómo se consuma
esa API no es parte de este alcance.

1. Un endpoint recibe un PDF y devuelve un **JSON de preview** con los datos extraídos,
   confianza por campo y conceptos sin match (no persiste nada).
2. Otro endpoint recibe ese JSON (posiblemente corregido por el caller antes de
   reenviarlo) y, según el modo indicado, valida o valida-y-crea la liquidación.

**Esto NO es un proyecto desde cero, y el split de pasos ya existe en el código.**
El flujo actual de `POST /payroll/import` ya encadena dos use cases separados:

```python
# 1) ImportPayroll.from_bytes() -- application/use_cases/import_payroll.py
async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
    rows = self._importer.read_rows(filename, content)   # parsear -> filas
    return await self._repository.import_rows(rows)      # persistir filas crudas

# 2) ProcessImportedPayrollPeriods.execute() -- corre después, en la misma request
#    Calcula AFP/salud/cesantía/impuesto contra pf-rates, compara declarado vs.
#    calculado, y arma expected_net_pay_clp / net_pay_difference_clp /
#    net_pay_warning / contribution_validation.
#    -> ACÁ es donde hoy vive el "cuadre de cuentas". No en import_rows().
```

El nuevo flujo PDF debe calzar en esta misma costura, no crear un pipeline paralelo:

- **Endpoint 1 (preview, sin persistir):** un extractor nuevo (adapter de
  infraestructura) que lee el PDF y produce filas candidatas — análogo a `read_rows()`,
  pero enriquecido con confianza por campo y estado de match contra el catálogo de
  conceptos (ver sección 3). **No debe llamar a `repository.import_rows()` ni a
  `ProcessImportedPayrollPeriods`.** No hay escritura en base de datos en este paso, y el
  cuadre de cuentas real (que depende de pf-rates) no corre acá.
- **Endpoint 2 (confirmar, con dos modos):** reusa **exactamente la misma secuencia** que
  ya usa `/payroll/import` hoy (`from_rows()` + `ProcessImportedPayrollPeriods.execute()`),
  sin reimplementarla. Recibe `list[ImportPayrollRowDTO]` como JSON en vez de un archivo.
  Soporta dos modos sobre esa misma secuencia:
  - `validate`: corre los dos use cases dentro de una transacción de DB y hace
    `ROLLBACK` al final. Devuelve el mismo resultado (warnings, diffs, totales
    calculados) pero no persiste nada.
  - `commit`: igual, pero hace `COMMIT`.

  Correr el mismo código y decidir commit/rollback al final (en vez de mantener dos
  implementaciones de validación) evita que "validar" y "persistir" diverjan con el
  tiempo. Ver sección 2 para el detalle de diseño y una limitación real a documentar:
  `ProcessImportedPayrollPeriods` llama a pf-rates para resolver market data faltante, y
  ese llamado puede cachear datos en la base de **pf-rates** (otro servicio, otra DB) —
  un rollback del lado de pf-payroll no deshace eso. Es inofensivo (son datos públicos de
  tipo de cambio/UTM), pero el modo `validate` no es 100% libre de efectos secundarios de
  punta a punta, y hay que decirlo explícito.

Puntos de extensión concretos a reusar (leer antes de proponer nada):

- `payroll/application/ports/importers.py` → `PayrollImporter(Protocol)`, método
  `read_rows(filename, content) -> list[ImportPayrollRowDTO]`. El extractor de PDF del
  endpoint 1 se inspira en este puerto, pero como el resultado incluye confianza y
  conceptos sin match, probablemente amerite su propio DTO más rico en vez de forzar el
  `ImportPayrollRowDTO` plano — ver sección 3.
- `application/dto.py` → `ImportPayrollRowDTO` (una fila por `concept_code` +
  `amount_clp` por período/empleador). Es el formato de entrada que el endpoint 2 espera.
- `GET /reference-data/payroll-concepts` → catálogo **cerrado y seedeado** de
  `concept_code`. Hoy no existe un concepto genérico "otros haberes/otros descuentos"
  (ver sección 4).
- `application/use_cases/import_payroll.py` → `ImportPayroll.from_bytes()`. El
  endpoint 2 necesita un entry point hermano (ej. `from_rows(rows:
  list[ImportPayrollRowDTO])`) que llame directo a `self._repository.import_rows(rows)`,
  sin pasar por ningún `PayrollImporter`.
- `application/use_cases/process_imported_payroll_periods.py` →
  `ProcessImportedPayrollPeriods.execute()`. Es el use case que hace el cuadre de cuentas
  real hoy. El endpoint 2 lo reusa sin tocarlo, tanto en modo `validate` como `commit`.

Adjunto mi última liquidación como ejemplo real, guardada **fuera del repo git**
(ver restricción de PII abajo): `modules/pf-payroll/secrets/Liquidación_202608.PDF`

## Restricciones importantes

- **PII — no negociable.** Las liquidaciones contienen RUT, sueldo, previsión y salud.
  Ningún PDF real ni JSON/fixture derivado de un PDF real puede vivir en una carpeta
  trackeada por git, ni siquiera temporalmente. Usar una carpeta fuera del repo (o un
  directorio `secrets/`-style ya gitignoreado, como `pf-db/secrets/` en este mismo
  ecosistema). Los fixtures de test que sí se commiteen deben tener datos
  sintéticos/anonimizados.
- El formato de una liquidación puede cambiar de un mes a otro sin aviso (conceptos
  nuevos, orden distinto, cambio de diseño).
- Entre empleadores distintos el formato puede ser completamente diferente.
- Los PDFs pueden ser digitales (con texto seleccionable) o escaneados (imagen).
- El endpoint 1 (preview) **no debe persistir nada** ni tener side effects sobre otras
  liquidaciones existentes — es idempotente y seguro de llamar repetidas veces con el
  mismo PDF.
- El endpoint 2 (confirmar) en modo `validate` debe dejar la base de datos de pf-payroll
  intacta (rollback real, no un chequeo aparte). El único efecto secundario aceptado y
  documentado es el cacheo de market data en pf-rates (ver arriba).
- Cualquier propuesta de infraestructura nueva (OCR, LLM, servicio de extracción) debe
  indicar su **costo estimado** y alternativas más baratas evaluadas (regla de este
  ecosistema: la opción más barata gana salvo justificación explícita). Si la solución
  usa un LLM, debe pasar por **AI Innovation Lab / AI Launchpad**, no un proveedor
  externo directo.
- Arquitectura hexagonal existente: `interfaces → application → domain`,
  `infrastructure → application`. `domain/` no tiene I/O. Los puertos son
  `typing.Protocol`. `Decimal` para todo monto, nunca `float`. Sin `assert` para
  validación de producción — usar `application/errors.py`. Sin "silent fallbacks": un
  campo con baja confianza se marca explícitamente en la respuesta, nunca se adivina.

## Lo que necesito

1. **Análisis del PDF adjunto**
   - Identifica todos los campos presentes y agrúpalos: datos del empleador, datos del
     trabajador, período, haberes imponibles, haberes no imponibles, descuentos legales
     (AFP, salud, seguro de cesantía, impuesto único), otros descuentos y totales.
   - Mapea cada campo identificado a un `concept_code` existente en
     `GET /reference-data/payroll-concepts`. Señala cuáles no tienen match.
   - Señala qué partes del documento parecen estables y cuáles son probablemente
     variables.

2. **Diseño de los dos endpoints**
   - **Endpoint 1 (preview):** firma, request/response, y por qué no debe tocar
     `PayrollRepository` ni `ProcessImportedPayrollPeriods`. La respuesta debe incluir,
     por cada fila candidata: valor extraído, `concept_code` propuesto (o `null` si no
     matchea), score de confianza, y el texto original de la etiqueta en el PDF.
   - **Endpoint 2 (confirmar):** firma, request (el JSON, forma =
     `list[ImportPayrollRowDTO]` o un wrapper directo, más un campo `mode: "validate" |
     "commit"`), y cómo delega 100% en `from_rows()` + `ProcessImportedPayrollPeriods`
     — sin reimplementar validación ni cómputo de contribuciones. Detalla el mecanismo
     de transacción (dry-run con `ROLLBACK` vs. `COMMIT`).
   - Indica si conviene reusar `POST /payroll/import` (aceptando también JSON, no solo
     multipart, y el nuevo parámetro `mode`) o crear rutas nuevas (ej.
     `POST /payroll/import/pdf-preview` y `POST /payroll/import/rows`). Justifica la
     elección.

3. **Manejo de conceptos sin match** *(gap real, no asumido)*
   - Hoy no existe un concepto catch-all para "otros haberes/otros descuentos". Compara
     opciones: (a) agregar conceptos genéricos al catálogo seedeado vía migración en
     pf-db, (b) mapeo manual obligatorio por plantilla de empleador con fallback a marcar
     la fila como no resuelta en la respuesta, (c) dejar la fila sin `concept_code` en el
     JSON de preview y que el endpoint 2 la rechace explícitamente si llega sin resolver.
   - Indica el impacto de cada opción en el modo `validate`/`commit` del endpoint 2 (por
     ejemplo: ¿debe `validate` fallar si quedan filas sin `concept_code`, o devolver un
     warning?).

4. **Opciones de extracción (solo para el endpoint 1 — preview)**
   Compara al menos: extracción por plantilla (coordenadas/regex/anclas), extracción con
   LLM con esquema JSON de salida forzado, OCR para escaneados, e híbrido. Para cada uno:
   precisión esperada, tolerancia a cambios de formato, **costo mensual estimado** al
   volumen indicado en "Stack", complejidad de implementación/mantenimiento, y cómo
   testearlo con `--cov-fail-under=100` sin datos reales (fixtures dorados + mocks del
   proveedor). Recuerda: errores de extracción acá son de bajo riesgo porque el
   endpoint 2 vuelve a validar antes de persistir — no hace falta 100% de precisión en el
   endpoint 1, sí buena señal de confianza.

5. **Sistema de plantillas versionables**
   - Formato y ubicación: deben vivir en `pf-payroll/infrastructure` (config
     versionada en git, JSON/YAML), **no en pf-db**.
   - Cómo detectar automáticamente qué plantilla aplica a un PDF.
   - Cómo versionar plantillas cuando el formato cambia, y fallback si ninguna calza
     (en el peor caso, el JSON de preview vuelve casi vacío — el endpoint 1 nunca debe
     romper por esto).
   - Cómo crear/ajustar una plantilla sin programar.

6. **Testing del modo validate/commit**
   - Cómo probar que el modo `validate` efectivamente no deja residuos en la base
     (ej. test que corre `validate`, cuenta filas antes/después, corre `commit`, y
     recién ahí ve el cambio).
   - Cómo mockear el llamado a pf-rates en estos tests para no depender de red.

7. **Plan de acción**
   Por etapas, con el endpoint 1 (preview) como MVP entregable de forma independiente,
   seguido del endpoint 2 en modo `commit` únicamente, y luego el modo `validate`.
   Indica riesgos y qué requiere coordinación cross-repo con pf-db (si se elige la
   opción 3a).

## Formato de respuesta

- **Entregable:** esta respuesta debe ser un archivo Markdown nuevo (no una respuesta
  inline ni un PR de código), ubicado en la **misma carpeta que este documento**
  (`docs/proposals/`).
- Empieza con el análisis del PDF adjunto, incluyendo el mapeo a `concept_code`
  existentes y la lista de campos sin match.
- Usa tablas para la comparación de opciones (extracción y manejo de conceptos sin
  match), incluyendo costo estimado.
- Incluye un ejemplo del JSON de preview (endpoint 1, con confianza y campos sin match) y
  un ejemplo del JSON de confirmación (endpoint 2, forma `list[ImportPayrollRowDTO]` +
  `mode`) — usando nombres de campo genéricos, sin montos ni RUT reales de mi
  liquidación.
- Termina con tu recomendación y por qué.

## Stack

- Lenguaje: Python (FastAPI, hexagonal, ya establecido en pf-payroll).
- Almacenamiento: PostgreSQL vía pf-db (mismo esquema de conceptos/períodos/ítems ya
  existente). El endpoint 1 (preview) no escribe en base de datos; el endpoint 2 en modo
  `validate` escribe y hace rollback dentro de una transacción.
- Servicio de IA/OCR: ninguno contratado todavía — evaluar vía AI Innovation Lab si se
  elige la ruta LLM.
- Volumen esperado: [completar: liquidaciones/mes].

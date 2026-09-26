# Investigación: discrepancia en `HEALTH_ADDITIONAL_UF` en una importación real

**Estado:** causa raíz aislada -- **no es un bug de código**. El motor de
cálculo de pf-payroll y el dato de UF de pf-rates quedaron demostrados
correctos; la brecha se redujo a una pregunta de datos de referencia
(`contracted_uf` en `health_plans`) que requiere verificación contra el
contrato Isapre real del empleado, fuera del alcance de este repo. Ver la
sección "Cierre" más abajo.
**Abierto:** 2026-09-26, justo después de deployar los commits `48a6314` y
`551cf30` (ver `git log` en `pf-payroll`). **Causa raíz aislada el mismo
día.**

## Contexto

Esta sesión subió dos fixes a `POST /payroll/import/rows`:

1. `48a6314` — `build_imported_contribution_validation()` ya no anula
   `expected_health_plan_additional_clp` solo porque un período tiene más de
   un `health_plan_id` asignado (ese guard era anterior a la agregación de
   `contracted_uf` entre todos los planes asignados que hace
   `get_contribution_context()`, y se disparaba en casi cualquier importación
   real).
2. `551cf30` — los campos monetarios de `ImportedPeriodRead` /
   `ImportedContributionValidationRead` ahora se muestran como números JSON
   de verdad, en vez del string entre comillas que pydantic serializa por
   default para `Decimal`.

Ambos fixes están confirmados funcionando en producción: los números vuelven
sin comillas, y la validación de `HEALTH_ADDITIONAL_UF` ahora sí corre en vez
de devolver `null` en silencio. Justamente *por* correr, salió a la luz una
discrepancia real.

## El hallazgo

Al volver a correr el mismo comprobante real (`WALMART-CHILE`, período
2026-08, `payment_date=2026-08-31`) contra `POST /payroll/import/rows`
(`mode=validate`) en producción:

```
declared_health_plan_additional_clp:  38013
expected_health_plan_additional_clp:  33516
health_plan_additional_difference_clp: 4497   <- lo calculado es MENOR que lo declarado
warning: "Imported contribution totals do not match the computed payroll
          contributions. HEALTH_ADDITIONAL_UF declared 38013.00 CLP,
          expected 33516 CLP."
```

Los otros tres conceptos reconciliados en la misma respuesta coincidieron
**exacto** (diff `0` en los tres):

- `PENSION_BASE`: declarado 367864 == esperado 367864
- `PENSION_ADDITIONAL`: declarado 42672 == esperado 42672
- `HEALTH_BASE`: declarado 257505 == esperado 257505

Ese es un dato importante: descarta que la tasa de cotización, el tope
(cap) o la base imponible topada estén mal — esos valores son insumos
compartidos entre pensión y salud. La discrepancia está aislada
específicamente al cálculo de `HEALTH_ADDITIONAL_UF` (el "adicional" de
plan Isapre denominado en UF).

## Dónde vive el cálculo

`src/payroll/domain/contribution_calculator.py`, método
`ContributionCalculator.health()`:

```python
contracted_clp = quantize_clp(plan.contracted_uf * plan_uf_value_clp)
additional_amount = max(Decimal("0"), contracted_clp - base_amount)
```

Como `base_amount` ya se probó correcto (`HEALTH_BASE` coincidió exacto), la
discrepancia tiene que estar completamente en `contracted_clp`, es decir, en
uno de estos dos valores:

- `plan.contracted_uf` — el valor sembrado en la tabla `health_plans` para
  el/los plan(es) asignados a este período, o
- `plan_uf_value_clp` — el tipo de cambio UF de cierre de mes resuelto para
  `2026-08-31` (ver el caveat ya documentado sobre el cacheo de market data
  de pf-rates, en el docstring de `ImportPayrollRowsRequest`).

`plan.contracted_uf` acá es el **agregado** entre todos los `health_plan_id`
asignados como snapshot al período (ver `get_contribution_context()` en
`payroll_repository_commands.py`, que suma `contracted_uf` entre todos los
planes asignados siempre que compartan la misma institución — exactamente el
comportamiento de agregación en el que el fix `48a6314` empezó a confiar en
vez de descartarlo de entrada).

## Duda planteada por el usuario: ¿por qué no hay warning también en `net_pay`?

Buena pregunta, y tiene una respuesta concreta en el código, no es un
descuido: `net_pay_difference_clp` y `net_pay_warning` **no** se calculan a
partir de los valores recién computados por `ContributionCalculator`
(los `33516` de la fórmula). Se calculan así
(`_reconcile_period_net_pay()` en `payroll_repository_shared.py`):

```python
summary_result = await self._session.execute(
    select(PayrollSummaryModel.net_pay_clp).where(
        PayrollSummaryModel.period_id == period.id
    )
)
expected_net_pay_clp = summary_result.scalar_one_or_none()
...
period.net_pay_difference_clp = (
    period.declared_net_pay_clp - period.expected_net_pay_clp
)
```

`PayrollSummaryModel.net_pay_clp` sale de la vista materializada
`PAY_MV_SUMARY`, cuya definición SQL (`pf-db/db/01_schema.sql`) es
simplemente:

```sql
SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END) -
SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS net_pay_clp
```

Es decir: `expected_net_pay_clp` es la suma de los **ítems ya persistidos y
declarados** (los `PAY_ITEM` que vinieron del PDF/CSV/JSON importado,
incluyendo el `38013` declarado de `HEALTH_ADDITIONAL_UF`), no la suma de los
valores que `ContributionCalculator` recalcula de forma independiente.

En otras palabras, son **dos chequeos distintos que responden preguntas
distintas**:

1. `contribution_validation` (el que dio warning) responde: *"¿el monto que
   el comprobante declaró para este concepto coincide con lo que nuestra
   propia fórmula de cálculo produciría?"*
2. La reconciliación de `net_pay` responde: *"¿los montos que el comprobante
   declaró para cada ítem suman exactamente el líquido a pagar que el mismo
   comprobante declaró?"*

Como el comprobante real ya viene internamente consistente (sus propios
números ya suman bien entre sí, es un comprobante real del empleador),
la pregunta (2) siempre va a dar `0` de diferencia sin importar si alguno de
sus conceptos individuales coincide o no con nuestra fórmula interna. Por
eso es totalmente posible (y no es un bug) tener un warning en
`contribution_validation` sin tener, a la vez, diferencia ni warning en
`net_pay`.

Dicho de otra forma: si algún día quisiéramos que un mismatch de
`HEALTH_ADDITIONAL_UF` como este también se reflejara en `net_pay`,
haría falta cambiar la reconciliación de `net_pay` para que use los montos
*recalculados* por `ContributionCalculator` en vez de los montos
*declarados* que ya están persistidos como `PAY_ITEM` — un cambio de diseño
bastante más grande que el bug puntual que estamos investigando acá, y que
no está claro que sea deseable (mezclaría "el comprobante es internamente
consistente" con "el comprobante coincide con nuestra fórmula", que hoy son
preguntas separadas a propósito).

## Avance (sesión 2, 2026-09-26): datos reales de `/reference-data`

Se consultó producción (paso 1 de los próximos pasos, solo lectura):

```
GET /reference-data/health-institutions?include_inactive=true
```
Única institución Isapre activa: `ESENCIAL` (`mandatory_rate=0.07`, coincide
con lo esperado). El resto (`BANMEDICA`, `COLMENA`, `CONSALUD`, `CRUZBLANCA`,
`FONASA`, `NUEVA_MASVIDA`, `VIDA_TRES`) están inactivas.

```
GET /reference-data/health-plans?include_inactive=true
```
Existen exactamente 3 planes, los tres bajo `ESENCIAL`, los tres con
`valid_from=2024-11-01` y `valid_to=null` (o sea, los tres siguen vigentes
al `2026-08-31` -- esto confirma que `_deduce_health_plan_ids_for_date`
efectivamente resuelve los 3, no uno solo, para el paso 2):

| id | plan_name    | contracted_uf |
|----|--------------|---------------|
| 7  | Adicionales  | 0.79          |
| 8  | Base         | 5.42          |
| 9  | GES          | 0.91          |

Agregado total (lo que `get_contribution_context()` suma, misma
institución): **7.12 UF**.

### Reconstrucción a mano (paso 4, con Decimal exacto)

Como `base_amount_clp` ya está confirmado correcto (`HEALTH_BASE` coincide
exacto en 257505), y `additional_amount = contracted_clp - base_amount`,
podemos despejar qué valor de UF *implica* cada lado sin conocer todavía el
valor real que devolvió pf-rates:

```
uf_value implícito en el CALCULADO (33516) = (33516 + 257505) / 7.12
                                            = 40873.7359550561797752808988764...

uf_value implícito en el DECLARADO (38013) = (38013 + 257505) / 7.12
                                            = 41505.3370786516853932584269663...

diferencia = 631.60 CLP por UF  (~1.545% más alto en el declarado)
```

Esto es un hallazgo bastante limpio: **si el agregado de 7.12 UF es
correcto**, toda la brecha de 4497 CLP se explica con una sola variable --
un valor de UF ~1.55% más alto usado por quien emitió el comprobante real
que el que nuestro sistema resolvió para `2026-08-31`. Los dos valores
implícitos (~40874 y ~41505 CLP/UF) son, además, perfectamente plausibles
para una UF de mediados de 2026 -- no son números disparatados que
sugieran que el agregado de 7.12 UF esté mal.

Esto reordena las hipótesis: ahora el sospechoso principal es puntualmente
el **valor de UF resuelto para `2026-08-31`** (hipótesis 2 original), no la
composición del agregado de planes (hipótesis 3 original) -- una brecha
consistente de ~1.55% en un solo número (el precio de la UF) es una
explicación más simple y más limpia que andar adivinando qué subconjunto de
los 3 planes sumar.

## Cierre (sesión 2, continuación): el valor de UF está descartado como causa

Se consultó pf-rates directamente:

```
GET /exchange-rates/value?currency_code=UF&rate_date=2026-08-31  (en pf-rates)
-> { "value_clp": 40873.770000 }
```

Reemplazando este valor **real y confirmado** en la fórmula del dominio
(con el mismo redondeo que usa `quantize_clp`, `ROUND_HALF_UP` a 0
decimales):

```
contracted_clp = 7.12 UF * 40873.77 = 291021.2424
additional_amount = round(291021.2424 - 257505) = 33516
```

**Coincide exacto con el `expected_health_plan_additional_clp: 33516` que
devolvió la API.** Esto confirma sin ambigüedad que:

- `resolve_month_end_uf_exchange_rate()` está leyendo el valor correcto
  para `2026-08-31`.
- pf-rates tiene el dato correcto para esa fecha.
- La fórmula de `ContributionCalculator.health()` está aplicando ese valor
  correctamente.

Es decir: **no hay ningún bug de código en pf-payroll ni dato
desactualizado en pf-rates.** La hipótesis 1 (valor de UF) queda
**descartada por completo**, con evidencia exacta, no aproximada.

El usuario además buscó manualmente si `41505.34` CLP (el UF implícito en
lo *declarado* por el comprobante) coincide con el UF de **algún** otro
día cercano -- **no coincide con ninguno**. Esto descarta también, de forma
independiente, la variante más débil de la hipótesis 1 ("está usando la
fecha equivocada, pero un día real"): no hay ninguna fecha real de UF que
reproduzca el monto declarado usando el agregado de 7.12 UF.

### La brecha real, con el UF ya confirmado

Con el UF fijo en `40873.77` (el valor correcto y confirmado), lo único que
puede explicar los `38013` declarados es que el `contracted_uf` total
realmente contratado por este empleado no sea `7.12` sino:

```
contracted_uf implícito en lo declarado = (38013 + 257505) / 40873.77
                                         = 7.23001572891367740240256771029...

gap vs nuestro agregado sembrado (7.12) = 0.11001572891367740240256771029... UF
```

Es decir, al empleado real parecería faltarle **~0.11 UF** de plan
contratado en la tabla `health_plans` respecto de lo que el comprobante
real factura. Ninguno de los 3 planes existentes (`0.79`, `5.42`, `0.91`)
es ese valor ni una combinación obvia de ellos -- no es que falte sumar uno
de los 3 que ya existen, sino que posiblemente **falta un cuarto
componente** en la data de referencia, o alguno de los 3 valores sembrados
está levemente desactualizado.

## Hipótesis (cierre)

1. ~~Valor de UF resuelto para `2026-08-31` incorrecto.~~ **Descartada**,
   confirmada con dato real de pf-rates + búsqueda manual del usuario en
   otras fechas.
2. **[única hipótesis viva] Dato sembrado `contracted_uf` incompleto o
   desactualizado para el plan Isapre `ESENCIAL` de este empleado.** Con
   el UF ya confirmado, la brecha entera (4497 CLP) se explica con un
   faltante de ~0.11 UF en el total contratado sembrado en `health_plans`
   (7.12 sembrado vs ~7.23 real implícito). Esto ya **no es una
   investigación de código** -- es una pregunta de datos/negocio: hay que
   verificar contra el contrato Isapre real de este empleado (o contra el
   emisor del comprobante) si le falta un cuarto componente de plan en la
   tabla `health_plans`, o si alguno de los 3 valores sembrados
   (`Adicionales=0.79`, `Base=5.42`, `GES=0.91`) debería ser distinto.
3. ~~Auto-deducción de múltiples planes tomando el conjunto
   equivocado.~~ **Descartada** como explicación de código: se confirmó que
   los 3 planes vigentes se resuelven y suman correctamente; la
   agregación en sí no está rota, simplemente el total sembrado no
   coincide con la realidad de este empleado.
4. ~~Redondeo/cuantización en `quantize_clp`.~~ **Descartada**: la
   reconstrucción exacta con el UF real reproduce el `33516` de la API al
   CLP exacto.

## Próximos pasos sugeridos

1. ~~Consultar en producción todos los planes de salud vigentes al
   `2026-08-31`.~~ **Hecho.**
2. ~~Confirmar qué `health_plan_ids` resuelve
   `_deduce_health_plan_ids_for_date`.~~ **Hecho.**
3. ~~Confirmar el `uf_value_clp` real resuelto para `2026-08-31`.~~
   **Hecho** -- `40873.77`, coincide exacto con el cálculo.
4. ~~Reconstruir a mano el cálculo.~~ **Hecho** -- reproduce `33516` al CLP
   exacto; la brecha se aisló 100% en `contracted_uf`.
5. **Pendiente (fuera de alcance de código):** confirmar contra el
   contrato Isapre real de este empleado, o contra quien emite el
   comprobante, si falta un cuarto componente de plan (`~0.11 UF`) en
   `health_plans`, o si alguno de los 3 valores sembrados está desfasado.
   Si se confirma un dato faltante/desactualizado, la corrección sería un
   `INSERT`/`UPDATE` de datos de referencia (coordinado según las reglas
   de `pf-db`), **no** un cambio de código en `pf-payroll` -- el motor de
   cálculo ya quedó demostrado correcto en esta investigación.

## Contexto previo relacionado

- `docs/proposals/pdf-import-design-recommendation.md` — diseño original de
  la importación de PDF, incluyendo el error de mapeo `COMISIÓN AFP` ->
  `HEALTH_ADDITIONAL_UF` arreglado esta misma semana (ver historial de git:
  `c152f1a`), que es un bug *distinto* y ya resuelto, no relacionado con
  este.
- El docstring de `ImportPayrollRowsRequest` en
  `src/payroll/interfaces/api/routes/payroll.py` — documenta el efecto
  secundario de cacheo de market data de pf-rates mencionado en la
  hipótesis 2.

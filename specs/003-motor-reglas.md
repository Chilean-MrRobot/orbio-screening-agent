# 003 — Motor de reglas eliminatorias

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000, 001

## Objetivo
Decidir de forma determinista (código, nunca LLM) si un candidato queda descartado por una regla eliminatoria de `config/rules/knockout.yaml`. La conversación lo llama después de cada respuesta, así que debe funcionar con la ficha a medio llenar.

## Alcance
- Incluye: evaluador de `Condition` con lógica de tres valores, registro de condiciones especiales (`license_compatible`, `city_in_service_area`) y `evaluate_knockouts`, que devuelve el veredicto, las reglas pasadas, las pendientes con sus campos faltantes y la plantilla del mensaje de descarte.
- No incluye: persistir el resultado en la ficha ni cambiar el estado o la etapa del candidato (008), redactar el mensaje con la plantilla (009), el puntaje (004) ni validar por barrio, código postal o distancia.

## Interfaz
Paquete `screening.rules`:

```python
def evaluate_knockouts(
    data: ScreeningData,
    country: Country,
    config: AppConfig,
    registry: SpecialConditionRegistry | None = None,  # por defecto, el registro del motor
) -> KnockoutEvaluation

class PendingRule(BaseModel):
    name: str
    missing_fields: list[str]          # rutas con punto, ordenadas

class KnockoutEvaluation(BaseModel):
    result: KnockoutResult | None      # contrato de la ola 0; None = aún sin veredicto
    passed_rules: list[str]            # en el orden del YAML
    pending_rules: list[PendingRule]
    message_template: str | None       # plantilla de la regla que falló

def evaluate_condition(condition: Condition, data: ScreeningData, context: EvaluationContext) -> Outcome

@dataclass(frozen=True)
class Outcome:
    value: bool | None                 # None = desconocido
    missing: frozenset[str]            # campos que harían falta para decidir

class SpecialConditionRegistry:
    def register(name, fn) -> None
    def get(name) -> SpecialConditionFn   # lanza UnknownSpecialConditionError

SpecialConditionFn = Callable[[ScreeningData, RegionConfig], Outcome]
```

Errores (todos heredan de `RulesEngineError`): `UnknownSpecialConditionError`, `MissingRegionError`, `RuleEvaluationError` (comparación entre tipos incompatibles).

## Comportamiento

### Valores y rutas
- Un campo se lee por ruta con punto (`delivery_experience.years`). Si un tramo intermedio es `None`, el valor es `None`.
- Un valor **falta** si es `None` o un texto vacío o solo con espacios.

### Operadores (lógica de tres valores)
- Si el campo falta, todo operador da **desconocido** con ese campo en `missing`, salvo `exists` (falso) y `not_exists` (verdadero), que siempre deciden.
- `equals`/`not_equals`: igualdad (los enums se comparan con su valor en texto). `greater_than`, `less_than`, `greater_or_equal`, `less_or_equal`: comparación contra `value`. `in`/`not_in`: pertenencia a `values`. `between`: `min <= x <= max`, ambos inclusive.
- Si la comparación no es posible por tipos (p. ej. texto contra número), lanza `RuleEvaluationError` nombrando el campo: es un error de configuración, no del candidato.

### Combinaciones (Kleene)
- `all`: falso si algún hijo es falso; verdadero si todos son verdaderos; si no, desconocido.
- `any`: verdadero si algún hijo es verdadero; falso si todos son falsos; si no, desconocido.
- `not`: invierte; desconocido sigue desconocido.
- Cuando el resultado es desconocido, `missing` es la unión de los campos faltantes de los hijos desconocidos; cuando es verdadero o falso, `missing` queda vacío.

### Condiciones especiales
Se registran en código por nombre. El registro por defecto trae:
- **`license_compatible`**: busca `license_type` en `licenses` de la región del país sin distinguir mayúsculas ni espacios extremos.
  - Falta `license_type` o `vehicle_type` → desconocido.
  - Licencia que no está en la tabla → falso.
  - Vehículo fuera de `vehicles` de esa licencia → falso.
  - Si la licencia tiene `min_years` para ese vehículo: falta `license_years` → desconocido (`missing = {license_years}`); `license_years < min_years` → falso; si no, verdadero.
- **`city_in_service_area`**: compara `city` con el `name` y los `aliases` de las ciudades de la región, normalizando ambos lados: sin mayúsculas, sin tildes y con los espacios colapsados. Sin coincidencia → falso.

### `evaluate_knockouts`
1. Si el país no tiene región en la configuración → `MissingRegionError`.
2. Antes de evaluar, verifica que toda condición especial usada por una regla habilitada esté registrada; si no → `UnknownSpecialConditionError` (falla aunque la regla todavía no se pueda evaluar).
3. Recorre las reglas **habilitadas** en el orden del YAML (las deshabilitadas no aparecen en la salida). La etapa (`stage`) de la regla no limita cuándo se evalúa: se evalúa apenas están sus datos.
   - Si le falta algún campo de `requires` → pendiente con esos campos; no se evalúa la condición.
   - Si la condición es verdadera → pasada.
   - Si es desconocida (p. ej. falta `license_years` para B y moto) → pendiente con los campos de `missing`.
   - Si es falsa → **se detiene**: `result = KnockoutResult(passed=False, failed_rule, reason_code)` y `message_template` de la regla. Las reglas siguientes no aparecen en la salida.
4. Si no falló ninguna y no quedan pendientes → `KnockoutResult(passed=True)`. Si quedan pendientes → `result = None`.

Una regla pendiente no impide evaluar las siguientes: si la ciudad está fuera de zona, el candidato se descarta aunque aún no haya dicho su licencia.

## Criterios de aceptación
- **Dado** una ficha vacía, **cuando** se evalúa, **entonces** `result` es `None` y las cuatro reglas están pendientes con sus `requires`.
- **Dado** `consent = false`, **cuando** se evalúa, **entonces** falla `consent_given` con `no_consent` y plantilla `disqualified_no_consent`.
- **Dado** ES, licencia "b", moto y 2 años, **cuando** se evalúa, **entonces** falla `license_matches_vehicle`; con 3 años, pasa; sin `license_years`, queda pendiente con `license_years`.
- **Dado** MX y ciudad "cdmx" o "Ciudad de Mexico", **cuando** se evalúa, **entonces** pasa `city_in_service_area`.
- **Dado** ES y ciudad "Toledo", **cuando** se evalúa, **entonces** falla con `out_of_service_area` aunque falten la licencia y el vehículo.
- **Dado** `has_license = false` y una ciudad fuera de zona, **cuando** se evalúa, **entonces** falla solo `has_license` (la primera en orden).
- **Dado** todos los datos válidos, **cuando** se evalúa, **entonces** `result.passed` es verdadero y no hay pendientes.
- **Dado** una regla con una condición especial no registrada, **cuando** se evalúa, **entonces** lanza `UnknownSpecialConditionError`.
- **Dado** un país sin región, **cuando** se evalúa, **entonces** lanza `MissingRegionError`.

## Tests
- `tests/unit/test_rules_conditions.py`: cada operador (verdadero, falso y desconocido), rutas con punto, `exists`/`not_exists` con datos faltantes, `all`/`any`/`not` con desconocidos y error de tipos.
- `tests/unit/test_rules_specials.py`: `license_compatible` y `city_in_service_area` con las regiones reales de ES y MX, normalización de ciudades y registro.
- `tests/unit/test_rules_engine.py`: cada regla del YAML real en ES y MX, evaluación incremental, corte en la primera falla, reglas deshabilitadas y errores claros.

## Evaluación (solo si usa LLM)
No aplica: el motor es determinista y no llama al LLM.

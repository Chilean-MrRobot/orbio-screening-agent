# 007 — Pipeline de evaluación (golden dataset + LLM-as-a-judge)

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000, 001, 006

## Objetivo
Medir con datos, y no a ojo, si las features que usan LLM responden lo esperado: correr una suite de casos (pregunta + respuesta esperada), comparar cada respuesta con la esperada usando un juez LLM y reportar accuracy, latencia y costo. Es la base que usarán el agente (009), el RAG (010), el resumen (012) y el clasificador (014).

## Alcance
- Incluye: datasets JSONL por suite, runner, juez LLM-as-a-judge, métricas, reporte JSON + Markdown, CLI, wrapper de pytest (`eval`) y la suite `judge_sanity` que valida al propio juez.
- No incluye: paralelismo, caché de respuestas, CI de evals, comparación entre corridas ni otras métricas (fidelidad, toxicidad…). Quedan como guía en `evals/README.md`.

## Interfaz

**Dataset** (`evals/datasets/<suite>.jsonl`, un caso por línea):
```json
{"id": "js-01", "input": {"question": "..."}, "expected": "...", "metadata": {"expected_verdict": "pass"}}
```
- `EvalCase`: `id: str`, `input: dict`, `expected: str`, `metadata: dict = {}`. Los `id` son únicos por suite.
- `metadata.expected_verdict` (`pass` | `fail`, por defecto `pass`): el veredicto correcto. Sirve para casos negativos y para evaluar al juez.
- `load_suite(name, datasets_dir=None) -> list[EvalCase]`. Un archivo inexistente, una línea inválida o un `id` repetido lanzan `DatasetError` con el número de línea.

**Objetivo** (lo que se evalúa): `Target = Callable[[EvalCase], TargetOutput]`, con `TargetOutput(answer: str, usage: LLMUsage | None)`.

**Juez** (`evals.judge`):
- `JudgeVerdict`: `reasoning: str`, `verdict: "pass" | "fail"`, `score: 1..5` (salida estructurada, en ese orden para que razone antes de decidir).
- `Judge(llm, model, prompt_path=prompts/judge.md)`, `judge(case, answer) -> (JudgeVerdict, LLMUsage)`. `prompt_version` es la versión declarada en el prompt más un hash corto del archivo.
- El juez ve la pregunta (`input.question` si existe; si no, el `input` completo en JSON), la respuesta esperada y la del sistema.

**Runner** (`evals.runner`): `run_suite(suite, cases, target, judge, *, clock, timer=time.perf_counter) -> SuiteReport`.

**Métricas** (`evals.metrics`): `compute_metrics(results) -> SuiteMetrics` con
- `total`, `passed`, `correct`, `errors`;
- `accuracy = correct / total` (veredicto igual al esperado) y `pass_rate = passed / total`;
- `latency_p50_ms` y `latency_p95_ms` del objetivo (percentil con interpolación lineal);
- `target` y `judge`: `UsageTotals` (`calls`, `input_tokens`, `output_tokens`, `cost_usd`) por separado, y `total_cost_usd`.

**Reporte** (`evals.report`): `write_report(report, results_dir=evals/results) -> (json_path, md_path)`, con nombre `<suite>-<YYYYMMDDTHHMMSSZ>`.

**Suites** (`evals.suites`): `SUITES: dict[str, SuiteSpec]`, donde `SuiteSpec` tiene `make_target(llm, config) -> Target` y `threshold` (accuracy mínima).

**CLI:** `python -m evals.run --suite <nombre> [--limit N] [--results-dir DIR]`. Imprime el resumen y las rutas del reporte; sale con código 1 si la accuracy queda bajo el umbral.

**Pytest:** `tests/evals/test_suites.py`, marcado `eval`, una prueba por suite que exige `accuracy >= threshold`; se salta sin `OPENAI_API_KEY`.

## Comportamiento
- Los casos corren en secuencia, en el orden del archivo.
- La latencia del caso es el tiempo de pared de la llamada al objetivo (no incluye al juez).
- Si el objetivo o el juez fallan en un caso, el caso queda con `error`, veredicto `fail`, cuenta como incorrecto y la suite sigue.
- El juez usa `llm.judge_model` de `config/client.yaml` y no envía `temperature`.
- La hora de la corrida sale del `Clock` inyectado.
- `judge_sanity`: el objetivo devuelve tal cual `input.answer` (no llama al LLM). Hay respuestas equivalentes con otras palabras (`expected_verdict: pass`) y respuestas que contradicen u omiten lo esencial (`fail`). Umbral: 0,9.

## Criterios de aceptación
- **Dado** un JSONL válido, **cuando** se carga, **entonces** se obtienen los casos en orden; **dado** un `id` repetido o una línea mal formada, **entonces** `DatasetError` indica la línea.
- **Dado** un juez que aprueba, **cuando** el caso espera `pass`, **entonces** es correcto; **cuando** espera `fail`, **entonces** es incorrecto.
- **Dado** un objetivo que lanza una excepción en un caso, **cuando** corre la suite, **entonces** ese caso queda con `error` y el resto se evalúa.
- **Dado** latencias 100, 200, 300 y 400 ms, **entonces** p50 = 250 ms y p95 = 385 ms.
- **Dado** uso del objetivo y del juez, **entonces** el reporte los suma por separado y `total_cost_usd` es la suma de ambos.
- **Dado** un reporte, **cuando** se escribe, **entonces** existen el JSON (relegible como `SuiteReport`) y el Markdown con el resumen y los casos incorrectos.
- **Dado** el prompt del juez, **cuando** se arma el mensaje, **entonces** contiene la pregunta, la respuesta esperada y la del sistema, y el juez pide salida estructurada `JudgeVerdict` al `judge_model`.
- **Dado** que la accuracy queda bajo el umbral, **cuando** se corre el CLI, **entonces** sale con código 1.

## Tests
En `tests/unit/eval_pipeline/`:
- `test_dataset.py`: carga, errores de formato, `id` repetidos, suites del repo válidas.
- `test_judge.py`: armado del prompt, modelo, versión del prompt, salida.
- `test_metrics.py`: percentiles, accuracy y pass rate, totales de uso, suite vacía.
- `test_runner.py`: correcto/incorrecto según `expected_verdict`, errores del objetivo y del juez, latencia, uso.
- `test_report.py`: JSON y Markdown.
- `test_cli.py`: CLI con `FakeLLMClient`, código de salida según umbral.

Todos usan `FakeLLMClient` (006); ninguno llama a la red.

## Evaluación
Suite `judge_sanity` (10 casos, umbral de accuracy 0,9):

| Tipo | Ejemplo | Veredicto esperado |
|---|---|---|
| Paráfrasis | Esperada: «Sí, se puede trabajar solo fines de semana». Respuesta: «Claro, hay turnos únicamente sábado y domingo». | pass |
| Contradicción | Esperada: «Se necesita carnet A1 o superior para moto de 125». Respuesta: «No hace falta carnet para moto». | fail |
| Omisión de lo esencial | Esperada: «Te contactará un reclutador en 48 h». Respuesta: «Gracias por postular». | fail |

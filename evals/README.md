# Evaluaciones

Pipeline custom de golden dataset + LLM-as-a-judge (spec `specs/007-pipeline-evaluacion.md`). Cada caso es una pregunta con su respuesta esperada; el sistema responde, un juez LLM compara ambas y se reportan **accuracy, latencia y costo en tokens**.

```
evals/
  datasets/<suite>.jsonl   un caso por línea
  suites.py                qué objetivo evalúa cada suite y su umbral
  targets/                 objetivos que llaman a la feature real (extraction, reply: agente 009)
  judge.py                 juez (prompt en prompts/judge.md)
  runner.py · metrics.py · report.py · run.py (CLI)
  results/                 reportes generados (ignorado por git)
```

## Correr

Requiere `OPENAI_API_KEY` en el entorno (y `LANGSMITH_TRACING=true` si se quieren trazas).

```bash
.venv\Scripts\python -m evals.run --suite judge_sanity
.venv\Scripts\python -m evals.run --suite judge_sanity --limit 3   # prueba barata
.venv\Scripts\python -m pytest -m eval                             # todas las suites, exige el umbral
```

El CLI sale con código 1 si la accuracy queda bajo el umbral de la suite.

## Agregar una suite

1. Crear `evals/datasets/<suite>.jsonl`:
   ```json
   {"id": "faq-01", "input": {"question": "¿Pagan las propinas?"}, "expected": "Sí, las propinas son íntegras para el repartidor.", "metadata": {"tags": ["faq"]}}
   ```
   - `input` es libre: el objetivo lo interpreta. El juez ve `input.question` si existe; si no, el `input` completo en JSON.
   - `metadata.expected_verdict: "fail"` marca un caso negativo (la respuesta correcta del juez es reprobarlo). Por defecto es `pass`.
2. Registrar en `evals/suites.py` un `SuiteSpec` con `make_target(llm, config)`, que devuelve una función `caso → TargetOutput(answer, usage)` que llama a la feature real (por ejemplo, el agente de la 009 con un candidato armado desde `input`), y el `threshold` de accuracy.
3. Probar con `--limit 2` antes de correr la suite completa.

La suite `judge_sanity` evalúa al propio juez: el objetivo devuelve la respuesta escrita en el caso. Si el prompt o el modelo del juez cambian, correrla primero; si baja de 90 %, el resto de los números no es confiable.

## Leer el reporte

Cada corrida deja `results/<suite>-<fecha>.json` (completo, para comparar o graficar) y `.md` (para leer):

- **Accuracy:** casos donde el veredicto del juez coincide con el esperado. Es la métrica principal.
- **Pass rate:** casos aprobados por el juez. En una suite sin casos negativos coincide con la accuracy.
- **Errores:** casos donde el objetivo o el juez lanzaron excepción; cuentan como incorrectos.
- **Latencia p50 / p95:** tiempo de pared del objetivo por caso (sin el juez).
- **Objetivo / Juez:** llamadas, tokens y costo por separado, para saber cuánto cuesta el sistema y cuánto la medición.
- **Casos incorrectos:** respuesta esperada, la del sistema y el razonamiento del juez. Leer el razonamiento antes de culpar al sistema: a veces el error es del dataset o del juez.
- El reporte guarda el modelo del juez y la versión del prompt (`v<versión>-<hash>`) para que dos corridas sean comparables.

## Escalar después

Fuera del alcance del demo, en orden de prioridad:

1. **Sumar casos desde fallas reales.** Cada conversación que salió mal en producción (o que un reclutador corrigió en el CRM) se convierte en un caso con su respuesta esperada. Es la forma más barata de que el dataset represente el uso real.
2. **Versionar datasets.** Cambiar un caso cambia la métrica: registrar la versión del dataset en el reporte (hash del JSONL o etiqueta `v2`) y no editar casos sin dejar constancia. Para datasets grandes, moverlos a LangSmith Datasets o a un bucket versionado.
3. **Cachear respuestas.** Guardar en `evals/.cache/` la respuesta del objetivo y del juez por hash de (caso, prompt, modelo) para no pagar dos veces lo que no cambió; al tocar solo el juez, reusar las respuestas del sistema.
4. **Correr en CI.** Un job a pedido o nocturno (no en cada push) con un secreto `OPENAI_API_KEY`, que publique el Markdown como comentario del PR y falle si la accuracy baja del umbral o cae respecto de `main`.
5. **Paralelizar** los casos con un pool de hilos (respetando el rate limit) cuando las suites pasen de unas decenas de casos.
6. **Más métricas:** accuracy por etiqueta de `metadata`, intervalo de confianza (con pocos casos, un punto porcentual no es señal), acuerdo del juez con etiquetas humanas y métricas específicas (fidelidad al FAQ en el RAG, campos correctos en la extracción, que el mensaje no mencione herramientas).

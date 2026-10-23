# Base de conocimiento (FAQ)

FAQ de Grupo Sazón para el RAG del agente (spec `specs/010-rag-faq.md`). **Todo el contenido es ficticio para el demo.**

- `faq/*.md`: un archivo por tema. Cada `## Pregunta` es un fragmento (pregunta + respuesta hasta el siguiente `##`); el título y el aviso no se indexan.
- `index.json`: fragmentos + embeddings + hash del corpus. Se versiona para no pagar embeddings en cada arranque.

## Editar la FAQ

1. Editar o agregar preguntas en `faq/`.
2. Reconstruir el índice (solo embebe los fragmentos nuevos o editados; requiere `OPENAI_API_KEY`):
   ```bash
   .venv\Scripts\python -m screening.knowledge.build
   ```
3. Subir `index.json` junto con el cambio. El test `test_versioned_index_is_up_to_date_with_the_faq` falla si se olvida, y `--check` sirve para CI sin llamar a la API.
4. Correr la suite: `.venv\Scripts\python -m evals.run --suite rag`.

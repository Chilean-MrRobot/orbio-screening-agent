"""Crea los índices compuestos de `deploy/firestore.indexes.json` (idempotente).

Uso: python deploy/create_firestore_indexes.py --project <id> [--database "(default)"]
Los índices se construyen en segundo plano (minutos); `--wait` espera a que estén listos.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_admin_v1 import FirestoreAdminClient
from google.cloud.firestore_admin_v1.types import Index

SPEC = Path(__file__).with_name("firestore.indexes.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--wait", action="store_true", help="espera a que estén READY")
    args = parser.parse_args()

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    admin = FirestoreAdminClient()
    created = existing = 0
    for collection in spec["collections"]:
        parent = f"projects/{args.project}/databases/{args.database}/collectionGroups/{collection}"
        for fields in spec["indexes"]:
            index = Index(
                query_scope=Index.QueryScope.COLLECTION,
                fields=[
                    Index.IndexField(field_path=path, order=Index.IndexField.Order[order])
                    for path, order in fields
                ],
            )
            try:
                admin.create_index(parent=parent, index=index)
                created += 1
            except AlreadyExists:
                existing += 1
    print(f"índices: {created} creados, {existing} ya existían")

    while args.wait:
        pending = []
        for collection in spec["collections"]:
            parent = (
                f"projects/{args.project}/databases/{args.database}/collectionGroups/{collection}"
            )
            pending += [
                i for i in admin.list_indexes(parent=parent) if i.state != Index.State.READY
            ]
        if not pending:
            print("todos los índices están READY")
            break
        print(f"esperando {len(pending)} índices…")
        time.sleep(20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

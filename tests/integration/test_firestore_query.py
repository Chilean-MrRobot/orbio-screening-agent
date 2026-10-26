"""Las mismas consultas y leases contra el Firestore real.

Se corre con `FIRESTORE_INTEGRATION_PROJECT=<proyecto>` y credenciales de gcloud (ADC).
Usa las colecciones `test_candidates` y `test_leases` (los índices compuestos van por nombre
de colección, ver `deploy/firestore.indexes.json`) y las vacía antes y después.
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.repository_contract import check_lease_contract, check_query_contract

PROJECT = os.environ.get("FIRESTORE_INTEGRATION_PROJECT")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not PROJECT, reason="falta FIRESTORE_INTEGRATION_PROJECT"),
]


@pytest.fixture
def client():
    from google.cloud import firestore

    client = firestore.Client(project=PROJECT)
    names = {"candidates": "test_candidates", "leases": f"test_leases_{uuid.uuid4().hex[:8]}"}

    def clean() -> None:
        for name in names.values():
            for doc in client.collection(name).list_documents():
                doc.delete()

    clean()
    yield client, names
    clean()


def test_firestore_query_contract(client) -> None:
    from screening.adapters.firestore import FirestoreCandidateRepository

    firestore_client, names = client
    check_query_contract(FirestoreCandidateRepository(firestore_client, names["candidates"]))


def test_firestore_lease_contract(client) -> None:
    from screening.adapters.firestore import FirestoreLeaseStore

    firestore_client, names = client
    check_lease_contract(FirestoreLeaseStore(firestore_client, names["leases"]))

from screening.adapters.memory import InMemoryCandidateRepository, InMemoryLeaseStore
from tests.repository_contract import check_lease_contract, check_query_contract


def test_memory_repository_query_contract() -> None:
    check_query_contract(InMemoryCandidateRepository())


def test_memory_lease_contract() -> None:
    check_lease_contract(InMemoryLeaseStore())

from screening.adapters.memory import InMemoryCandidateRepository
from screening.domain import Candidate, CandidateStatus


def test_save_and_get(repository: InMemoryCandidateRepository, candidate: Candidate) -> None:
    repository.save(candidate)

    assert repository.get(candidate.id) == candidate
    assert repository.get("missing") is None


def test_stored_copy_is_isolated_from_caller(
    repository: InMemoryCandidateRepository, candidate: Candidate
) -> None:
    repository.save(candidate)
    candidate.status = CandidateStatus.HIRED

    stored = repository.get(candidate.id)
    assert stored is not None
    assert stored.status == CandidateStatus.NEW


def test_get_by_phone(repository: InMemoryCandidateRepository, candidate: Candidate) -> None:
    repository.save(candidate)

    found = repository.get_by_phone("+34600111222")
    assert found is not None
    assert found.id == candidate.id
    assert repository.get_by_phone("+34000000000") is None


def test_list_filters_by_status(
    repository: InMemoryCandidateRepository, candidate: Candidate
) -> None:
    other = candidate.model_copy(update={"id": "other", "status": CandidateStatus.QUALIFIED})
    repository.save(candidate)
    repository.save(other)

    assert len(repository.list()) == 2
    assert [c.id for c in repository.list(CandidateStatus.QUALIFIED)] == ["other"]

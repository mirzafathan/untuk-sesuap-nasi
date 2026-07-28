from exceptions import DOMStructureChangedError, JobDetailStructureChangedError


def test_dom_structure_changed_error_is_an_exception() -> None:
    error = DOMStructureChangedError("missing cards")

    assert isinstance(error, Exception)
    assert str(error) == "missing cards"


def test_job_detail_structure_error_is_a_dom_maintenance_error() -> None:
    assert issubclass(JobDetailStructureChangedError, DOMStructureChangedError)

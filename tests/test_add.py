from add import add


def test_add_basic_behavior() -> None:
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

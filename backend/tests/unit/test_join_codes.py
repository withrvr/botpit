import random

from app.rooms.codes import ALPHABET, is_valid_code, new_join_code, normalise_code


def test_alphabet_excludes_ambiguous_letters() -> None:
    assert len(ALPHABET) == 32
    assert not set("ILOU") & set(ALPHABET)


def test_new_code_shape() -> None:
    code = new_join_code()
    assert len(code) == 6
    assert is_valid_code(code)


def test_normalise_maps_lookalikes() -> None:
    assert normalise_code(" ab-cd ef ") == "ABCDEF"
    assert normalise_code("ilo1") == "1101"
    assert not is_valid_code("ABCDE")
    assert not is_valid_code("ABCDEU")


def test_round_trip_random_codes() -> None:
    rng = random.Random(42)
    for _ in range(1000):
        code = new_join_code(rng)
        assert is_valid_code(code)
        assert normalise_code(code.lower()) == code

"""Mutated inputs must never crash the parser or break its invariants (see tests/fuzzing.py)."""
import pytest

from fuzzing import run


@pytest.mark.parametrize("seed", range(4))
def test_mutation_fuzz(seed):
    stats = run(seed, cases=150)
    assert stats["parsed"] > 0

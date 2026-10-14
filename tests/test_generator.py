"""Unit tests for the meter-read generator. No database needed."""

import random
from collections import defaultdict
from datetime import date

START = date(2026, 1, 1)


def make(gen, days=3, anomalies=True, seed=7):
    rng = random.Random(seed)
    return gen.generate_reads(START, days, gen.initial_registers(rng), rng, anomalies)


def test_clean_run_has_every_block(gen):
    rows, stats, _ = make(gen, days=3, anomalies=False)
    assert len(rows) == 3 * gen.BLOCKS_PER_DAY * len(gen.METERS)
    assert stats["dropped"] == stats["duplicated"] == stats["outage_missing"] == 0


def test_registers_never_go_backwards(gen):
    rows, _, _ = make(gen, days=5)
    last = defaultdict(float)
    for meter_id, *_, register, _kw in rows:
        assert register >= last[meter_id], f"{meter_id} register went backwards"
        last[meter_id] = register


def test_anomalies_are_injected(gen):
    _, stats, _ = make(gen, days=30)
    assert stats["dropped"] > 0
    assert stats["duplicated"] > 0


def test_same_seed_same_data(gen):
    a, _, _ = make(gen, seed=1)
    b, _, _ = make(gen, seed=1)
    assert a == b


def test_solar_is_dark_at_night(gen):
    rows, _, _ = make(gen, days=2, anomalies=False)
    night = [r for r in rows
             if r[0] == "MTR-SOL-GEN-01" and (r[3].hour < 5 or r[3].hour >= 20)]
    assert night and all(r[5] == 0 for r in night)


def test_blocks_are_stamped_at_block_end(gen):
    rows, _, _ = make(gen, days=1, anomalies=False)
    first = rows[0]
    assert (first[3].hour, first[3].minute) == (0, 15)

import random

from inktank import mode10


def roundtrip(cur, seed):
    data = mode10.encode_row(cur, seed)
    assert mode10.decode_row(data, seed) == cur, data.hex()
    return data


def test_identical_row_is_empty():
    row = [mode10.WHITE] * 100
    assert roundtrip(row, row) == b""


def test_random_rows():
    rng = random.Random(1)
    palette = [mode10.WHITE, 0, 0xFF0000, 0x00FF00, 0x123456 & mode10.MASK]
    for _ in range(2000):
        n = rng.randint(1, 700)
        seed = [rng.choice(palette) for _ in range(n)]
        cur = list(seed)
        for _ in range(rng.randint(0, 40)):
            a = rng.randrange(n)
            b = min(n, a + rng.choice([1, 2, 7, 8, 9, 260, 300, 600]))
            if rng.random() < 0.5:
                c = rng.choice(palette)
                cur[a:b] = [c] * (b - a)
            else:
                # values close to the seed row, to exercise the short deltas
                cur[a:b] = [(p + rng.randint(-8, 8) * 0x020202) & mode10.MASK if rng.random() < .5
                            else rng.randrange(1 << 24) & mode10.MASK for p in seed[a:b]]
        roundtrip(cur, seed)


def test_long_literal_and_offsets():
    n = 5000
    seed = [mode10.WHITE] * n
    cur = [(i * 2654435761) & mode10.MASK for i in range(n)]
    roundtrip(cur, seed)
    cur2 = list(seed)
    cur2[4000:4600] = [0] * 600   # long seed skip (VLI) + long RLE
    roundtrip(cur2, seed)


def test_numpy_encoder_matches_reference():
    import numpy as np
    rng = random.Random(3)
    palette = [mode10.WHITE, 0, 0xFF0000, 0x00FF00, 0x123456 & mode10.MASK]
    for _ in range(1500):
        n = rng.randint(1, 900)
        seed = [rng.choice(palette) for _ in range(n)]
        cur = list(seed)
        for _ in range(rng.randint(0, 40)):
            a = rng.randrange(n)
            b = min(n, a + rng.choice([1, 2, 7, 8, 9, 263, 264, 300, 520, 800]))
            if rng.random() < 0.4:
                cur[a:b] = [rng.choice(palette)] * (b - a)
            else:
                cur[a:b] = [(p + rng.randint(-8, 8) * 0x020202) & mode10.MASK if rng.random() < .5
                            else rng.randrange(1 << 24) & mode10.MASK for p in seed[a:b]]
        expected = mode10.encode_row(cur, seed)
        got = mode10.encode_row_np(np.array(cur, np.uint32), np.array(seed, np.uint32))
        assert got == expected

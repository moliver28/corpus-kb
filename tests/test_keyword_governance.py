from __future__ import annotations

from corpus_kb.coding.keyword_governance import criterial_owner, find_collisions, over_df_cap


def test_collision_and_cap_and_criterial() -> None:
    claims = {"sample": ["FW-25", "FW-20"], "control": ["FW-09"]}
    assert set(find_collisions(claims)) == {"sample"}
    assert over_df_cap(hit_chunks=1600, corpus_chunks=123000, cap_frac=0.012) is True
    owner = criterial_owner(
        "sample",
        {
            "FW-25": "inspect selected samples",
            "FW-20": "run analytics over the whole population",
        },
    )
    assert owner == "FW-25"

"""Recognize upstream history folded into second-generation squashes.

These are provenance mappings, not migration replacements. They never change
Django's graph or migration records. See contrib/container/IOT-Migration-Reconciliation.md
for the original Git objects and relationships used to verify each mapping.

Original Git blobs (available through ``git show <blob>``):
build nested squash: db2803f53fd060eed9f3d71689dd5e8f88335423;
part parallel 0061: c40b611b297621636a3e9126ffce737b97b66ddd;
part 0062 joining both branches: 4a8f4378f4c6a0ffc5c0caa4363d6221d318c65f.
"""

# outer squash, historical anchor in its replaces, historical ancestors
LEGACY_SQUASH_ANCESTORS = (
    (
        ('build', '0017_auto_20200426_0612_squashed_0058_buildline_consumed'),
        ('build', '0021_auto_20201020_0908_squashed_0026_auto_20201023_1228'),
        frozenset({
            ('build', '0021_auto_20201020_0908'),
            ('build', '0022_auto_20201020_0953'),
            ('build', '0023_auto_20201020_1009'),
            ('build', '0024_auto_20201020_1144'),
            ('build', '0025_auto_20201020_1248'),
            ('build', '0026_auto_20201023_1228'),
        }),
    ),
    (
        (
            'part',
            '0061_auto_20210103_2313_squashed_0142_remove_part_last_stocktake_remove_partstocktake_note_and_more',
        ),
        ('part', '0062_merge_20210105_0056'),
        frozenset({('part', '0061_auto_20210104_2331')}),
    ),
)


def recognized_legacy_ancestors(disk_migrations, applied_migrations):
    """Recognize ancestors only with their shipped and applied squash/merge.

    The extra part 0061 is a distinct parallel branch, joined by 0062; it is
    not an alias of the other 0061. Require the join (or its complete squash)
    to be applied before recognizing that branch's historical name.
    """
    recognized = set()
    for outer, anchor, ancestors in LEGACY_SQUASH_ANCESTORS:
        squash = disk_migrations.get(outer)
        if (
            squash is not None
            and anchor in (squash.replaces or [])
            and (anchor in applied_migrations or outer in applied_migrations)
        ):
            recognized.update(ancestors)
    return recognized

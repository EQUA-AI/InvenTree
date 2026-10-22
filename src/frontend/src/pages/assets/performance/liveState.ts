import type { KpiTile } from './KpiStrip';

/** A current tile is stale by either the source flag or its own configured budget. */
export function tileIsStale(
  tile: Pick<KpiTile, 'stale' | 'observedAt' | 'freshnessSeconds'>,
  now: number
) {
  return (
    tile.stale ||
    (tile.observedAt != null &&
      tile.freshnessSeconds != null &&
      tile.freshnessSeconds > 0 &&
      (now - tile.observedAt) / 1000 > tile.freshnessSeconds)
  );
}

export function staleTileCount(tiles: readonly KpiTile[], now: number) {
  return tiles.filter((tile) => tileIsStale(tile, now)).length;
}

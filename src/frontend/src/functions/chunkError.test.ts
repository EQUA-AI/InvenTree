import { isChunkLoadError } from '@lib/functions/ChunkError';
import { describe, expect, it } from 'vitest';

describe('page asset load errors', () => {
  it.each([
    'Failed to fetch dynamically imported module: https://example.test/old.js',
    'error loading dynamically imported module: https://example.test/old.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/old.css'
  ])('recognizes browser/Vite asset errors: %s', (message) => {
    expect(isChunkLoadError(message)).toBe(true);
  });

  it.each([
    null,
    'Failed to fetch',
    "Cannot read properties of undefined (reading 'name')"
  ])(
    'keeps ordinary application errors in the diagnostic fallback: %s',
    (message) => {
      expect(isChunkLoadError(message)).toBe(false);
    }
  );
});

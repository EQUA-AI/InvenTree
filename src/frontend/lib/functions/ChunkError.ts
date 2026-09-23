/** Browser/Vite failures while fetching a lazy JavaScript or CSS asset. */
export function isChunkLoadError(message: string | null): boolean {
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS for/i.test(
    message ?? ''
  );
}

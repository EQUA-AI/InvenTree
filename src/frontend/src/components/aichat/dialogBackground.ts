/** Make the DOM outside a portalled dialog inert, restoring previous values. */
export function isolateDialog(dialog: HTMLElement): () => void {
  const previous = new Map<HTMLElement, boolean>();
  const isolate = () => {
    let current: HTMLElement | null = dialog;
    while (current?.parentElement) {
      for (const sibling of current.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === current) continue;
        if (!previous.has(sibling)) previous.set(sibling, sibling.inert);
        sibling.inert = true;
      }
      current = current.parentElement;
      if (current === document.body) break;
    }
  };
  isolate();
  // Notifications and other portal siblings can mount while the dialog is open.
  const observer = new MutationObserver(isolate);
  observer.observe(document.body, { childList: true, subtree: true });
  return () => {
    observer.disconnect();
    for (const [element, inert] of previous) element.inert = inert;
  };
}

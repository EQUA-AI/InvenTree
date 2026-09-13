/** Bounded FIFO: accepted decision utterances are never evicted for newer speech. */
export class TurnQueue<T> {
  private items: T[] = [];
  constructor(
    private capacity: number,
    private onDropped: (item: T) => void
  ) {}
  push(item: T): boolean {
    if (this.items.length >= this.capacity) {
      this.onDropped(item);
      return false;
    }
    this.items.push(item);
    return true;
  }
  shift() {
    return this.items.shift();
  }
  clear() {
    this.items = [];
  }
  get length() {
    return this.items.length;
  }
}

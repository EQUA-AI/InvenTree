// Prevent the optional development inspector from mounting before importing
// the pinned renderer. No inspector, external sink, or content capture is used.
const runtime = globalThis as typeof globalThis & { [key: symbol]: unknown };
runtime[Symbol.for('openui.devtools.autoMount')] = true;

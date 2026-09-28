/**
 * Activate Lingui BEFORE any component module evaluates.
 *
 * The real app lazy-loads its pages, so LanguageContext activates the locale
 * long before a table module evaluates. This fixture imports components
 * statically, so the locale must be active during module evaluation or any
 * module-level translation call throws. Keep this import FIRST in
 * demo-metrics.tsx.
 */
import { i18n } from '@lingui/core';

import { messages } from '../src/locales/en/messages';

i18n.load('en', messages);
i18n.activate('en');

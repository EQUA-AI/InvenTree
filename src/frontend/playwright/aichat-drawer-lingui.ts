/**
 * Activate Lingui BEFORE any component module evaluates.
 * Keep this import FIRST in aichat-drawer.tsx.
 */
import { i18n } from '@lingui/core';

import { messages } from '../src/locales/en/messages';

i18n.load('en', messages);
i18n.activate('en');

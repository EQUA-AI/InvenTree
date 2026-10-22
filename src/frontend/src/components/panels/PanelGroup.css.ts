import { style } from '@vanilla-extract/css';
import { vars } from '../../theme';

export const selectedPanelTab = style({
  selectors: {
    '&[data-active]': {
      background: vars.colors.primaryColors.light
    }
  }
});

export const horizontalPanelList = style({
  paddingInline: 8,
  scrollPaddingInline: 8,
  maskImage:
    'linear-gradient(to right, transparent, black 8px, black calc(100% - 8px), transparent)',
  background: `linear-gradient(to right, var(--mantine-color-body), transparent) left center / 8px 100% no-repeat,
    linear-gradient(to left, var(--mantine-color-body), transparent) right center / 8px 100% no-repeat`,
  scrollbarWidth: 'thin'
});

export const densePanelTab = style({
  '@media': {
    '(max-width: 47.99375em)': { minWidth: 44, minHeight: 44, flexShrink: 0 }
  }
});

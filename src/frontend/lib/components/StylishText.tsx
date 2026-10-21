import { type MantineSize, Text } from '@mantine/core';
import type { JSX } from 'react';

// Use the native foreground token so headings remain readable in both schemes,
// including user-selected primary palettes with low-contrast gradient endpoints.
export function StylishText({
  children,
  size
}: Readonly<{
  children: JSX.Element | string;
  size?: MantineSize;
}>) {
  return (
    <Text
      data-stylish-heading
      fw={700}
      size={size ?? 'xl'}
      c='var(--mantine-color-text)'
    >
      {children}
    </Text>
  );
}

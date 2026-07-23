import { Anchor, Box, Code, Table, Text, Title } from '@mantine/core';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const markdownComponents: Components = {
  a: ({ children, href }) => {
    const isExternal = /^https?:\/\//i.test(href ?? '');

    return (
      <Anchor
        href={href}
        size='sm'
        target={isExternal ? '_blank' : undefined}
        rel={isExternal ? 'noopener noreferrer' : undefined}
      >
        {children}
      </Anchor>
    );
  },
  blockquote: ({ children }) => (
    <Box
      component='blockquote'
      mx={0}
      my='xs'
      pl='sm'
      py={2}
      style={{
        borderLeft: '3px solid var(--mantine-color-blue-4)',
        color: 'var(--mantine-color-dimmed)'
      }}
    >
      {children}
    </Box>
  ),
  code: ({ children, className }) => {
    const value = String(children);
    const isBlock = Boolean(className) || value.includes('\n');

    return isBlock ? (
      <Box
        component='code'
        className={className}
        style={{
          fontFamily: 'var(--mantine-font-family-monospace)',
          fontSize: 'var(--mantine-font-size-xs)'
        }}
      >
        {value.replace(/\n$/, '')}
      </Box>
    ) : (
      <Code fz='xs'>{children}</Code>
    );
  },
  h1: ({ children }) => (
    <Title order={1} size='h4' mt='sm' mb={4}>
      {children}
    </Title>
  ),
  h2: ({ children }) => (
    <Title order={2} size='h5' mt='sm' mb={4}>
      {children}
    </Title>
  ),
  h3: ({ children }) => (
    <Title order={3} size='h6' mt='xs' mb={4}>
      {children}
    </Title>
  ),
  h4: ({ children }) => (
    <Title order={4} size='sm' mt='xs' mb={4}>
      {children}
    </Title>
  ),
  h5: ({ children }) => (
    <Title order={5} size='sm' mt='xs' mb={4}>
      {children}
    </Title>
  ),
  h6: ({ children }) => (
    <Title order={6} size='sm' mt='xs' mb={4}>
      {children}
    </Title>
  ),
  hr: () => (
    <Box
      component='hr'
      my='sm'
      style={{ border: 0, borderTop: '1px solid var(--mantine-color-gray-3)' }}
    />
  ),
  ol: ({ children }) => (
    <Box component='ol' my={4} pl='lg'>
      {children}
    </Box>
  ),
  p: ({ children }) => (
    <Text component='p' size='sm' my={4} lh={1.6}>
      {children}
    </Text>
  ),
  pre: ({ children }) => (
    <Box
      component='pre'
      my='xs'
      p='xs'
      style={{
        backgroundColor: 'var(--mantine-color-gray-1)',
        border: '1px solid var(--mantine-color-gray-3)',
        borderRadius: 'var(--mantine-radius-sm)',
        overflowX: 'auto',
        whiteSpace: 'pre'
      }}
    >
      {children}
    </Box>
  ),
  table: ({ children }) => (
    <Box my='xs'>
      <Table.ScrollContainer minWidth={360}>
        <Table
          striped
          withTableBorder
          withColumnBorders
          horizontalSpacing='xs'
          verticalSpacing={4}
          fz='xs'
        >
          {children}
        </Table>
      </Table.ScrollContainer>
    </Box>
  ),
  ul: ({ children }) => (
    <Box component='ul' my={4} pl='lg'>
      {children}
    </Box>
  )
};

export function MarkdownMessage({
  content
}: Readonly<{
  content: string;
}>) {
  return (
    <Box
      style={{
        fontSize: 'var(--mantine-font-size-sm)',
        lineHeight: 1.6,
        overflowWrap: 'anywhere'
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={markdownComponents}
        skipHtml
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
}

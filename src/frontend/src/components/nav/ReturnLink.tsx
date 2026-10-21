import { t } from '@lingui/core/macro';
import { Anchor, Group } from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import { Link, useLocation } from 'react-router-dom';

import { readReturnPath } from './returnPath';

/** "Back to …" when this page was opened with a return path; else nothing. */
export function ReturnLink() {
  const location = useLocation();
  const path = readReturnPath(location.state);
  if (!path) return null;
  return (
    <Anchor component={Link} to={path.to} size='sm' data-return-link>
      <Group gap={4} wrap='nowrap'>
        <IconArrowLeft size={14} aria-hidden />
        {t`Back to ${path.label}`}
      </Group>
    </Anchor>
  );
}

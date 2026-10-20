import { t } from '@lingui/core/macro';
import { Anchor, Paper, Stack, Text } from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { workPanelParams } from './workPanelState';

/** Navigation only: readiness and the one update controller belong to Closeout. */
export function CloseoutLink() {
  const [params] = useSearchParams();
  return (
    <Paper withBorder p='sm' data-testid='closeout-link'>
      <Stack gap={4}>
        <Text size='sm' fw={600}>{t`Maintenance account`}</Text>
        <Text size='sm'>{t`Review the prepared account, report and update status in Closeout. Physical return remains separate.`}</Text>
        <Anchor
          component={Link}
          to={`?${workPanelParams(params, 'closeout')}`}
          size='sm'
        >
          {t`Open Closeout`}
        </Anchor>
      </Stack>
    </Paper>
  );
}

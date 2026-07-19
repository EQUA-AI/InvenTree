import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import { IconChecklist } from '@tabler/icons-react';
import { useMemo } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import type { PanelType } from '@lib/types/Panel';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { PartVerificationTable } from '../../tables/part/PartVerificationTable';

/**
 * Index page for listing Right-Part Finder verification sessions.
 */
export default function RightPartFinderIndex() {
  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'sessions',
        label: t`Verification Sessions`,
        content: <PartVerificationTable />,
        icon: <IconChecklist />
      }
    ];
  }, []);

  return (
    <Stack>
      <PageDetail title={t`Right-Part Finder`} actions={[]} />
      <PanelGroup
        pageKey='part-verification-index'
        panels={panels}
        model={ModelType.partverificationsession}
        id={null}
      />
    </Stack>
  );
}

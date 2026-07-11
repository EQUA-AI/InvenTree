import { t } from '@lingui/core/macro';
import { ActionIcon } from '@mantine/core';
import { forwardRef } from 'react';
import { NavLink } from 'react-router-dom';

import EquaLogo from '../../assets/branding/equa.png';

export const InvenTreeLogoHomeButton = forwardRef<HTMLDivElement>(
  (props, ref) => {
    return (
      <div ref={ref} {...props}>
        <NavLink to={'/'}>
          <ActionIcon size={28} variant='transparent'>
            <InvenTreeLogo />
          </ActionIcon>
        </NavLink>
      </div>
    );
  }
);

export const InvenTreeLogo = () => {
  return <img src={EquaLogo} alt={t`Equa AIMMS Logo`} height={28} />;
};

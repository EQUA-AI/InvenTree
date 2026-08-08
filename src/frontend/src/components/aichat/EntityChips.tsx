/**
 * Server-observed entity chips under an answer (S28, EX-ADR-004).
 *
 * Every chip comes from the server's entity manifest — records the turn was
 * actually about, resolved and mapped server-side. The client only renders
 * and navigates; a model string it does not recognize renders as an inert
 * label, never a guessed link.
 */

import { ModelInformationDict } from '@lib/enums/ModelInformation';
import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import { Anchor, Badge, Group } from '@mantine/core';
import { useNavigate } from 'react-router-dom';

import type { EntityChip } from '../../hooks/UseAIChat';

export function EntityChips({
  entities
}: Readonly<{ entities: EntityChip[] }>) {
  const navigate = useNavigate();

  if (!entities || entities.length === 0) {
    return null;
  }

  return (
    <Group gap={6} mt={8} data-testid='entity-chips'>
      {entities.map((entity) => {
        const model: ModelType | undefined = Object.values(ModelType).includes(
          entity.model as ModelType
        )
          ? (entity.model as ModelType)
          : undefined;
        const info = model ? ModelInformationDict[model] : undefined;
        const url =
          model && info?.url_detail ? getDetailUrl(model, entity.pk) : '';
        const label = entity.label || `${entity.model} #${entity.pk}`;
        const key = `${entity.model}:${entity.pk}`;

        if (!url) {
          // Unknown or route-less model: visible but deliberately inert.
          return (
            <Badge
              key={key}
              size='sm'
              variant='light'
              color='gray'
              data-testid={`entity-chip-inert-${key}`}
            >
              {label}
            </Badge>
          );
        }
        return (
          <Anchor
            key={key}
            href={url}
            onClick={(event) => navigateToLink(url, navigate, event)}
            underline='never'
          >
            <Badge
              size='sm'
              variant='light'
              color='blue'
              style={{ cursor: 'pointer' }}
              data-testid={`entity-chip-${key}`}
            >
              {label}
            </Badge>
          </Anchor>
        );
      })}
    </Group>
  );
}

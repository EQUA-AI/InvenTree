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
import { getDetailUrl } from '@lib/functions/Navigation';
import { Anchor, Badge, Group } from '@mantine/core';
import { Link } from 'react-router-dom';

import type { EntityChip } from '../../hooks/UseAIChat';
import { useAssistantNavigation } from './useAssistantNavigation';

export function EntityChips({
  entities
}: Readonly<{ entities: EntityChip[] }>) {
  const onNavigate = useAssistantNavigation();
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
          model &&
          info?.url_detail &&
          Number.isSafeInteger(entity.pk) &&
          entity.pk > 0
            ? getDetailUrl(model, entity.pk)
            : '';
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
            component={Link}
            to={url}
            onClick={onNavigate}
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

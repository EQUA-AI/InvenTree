import { describe, expect, it } from 'vitest';
import { notificationLink } from './notificationLink';

describe('notificationLink', () => {
  const route = '/machines/machine/8/health?anomaly=72';
  it('keeps the exact machine and alarm on an absolute backend link', () => {
    expect(
      notificationLink(
        `http://localhost:8000/web${route}`,
        'http://localhost:8000',
        'web'
      )
    ).toEqual({ href: `/web${route}`, route });
  });
  it('adds the frontend base once for native relative links', () => {
    for (const link of [route, `/web${route}`]) {
      expect(notificationLink(link, 'http://localhost:8000', 'web')).toEqual({
        href: `/web${route}`,
        route
      });
    }
  });
  it('supports a frontend mounted at the origin root', () => {
    for (const base of ['', '/']) {
      expect(notificationLink(route, 'http://localhost:8000', base)).toEqual({
        href: route,
        route
      });
      expect(
        notificationLink(
          `http://localhost:8000${route}`,
          'http://localhost:8000',
          base
        )
      ).toEqual({ href: route, route });
    }
  });
  it('preserves external links and handles a notification with no target', () => {
    expect(
      notificationLink(
        'https://example.com/alarm',
        'http://localhost:8000',
        'web'
      )
    ).toEqual({ href: 'https://example.com/alarm', route: null });
    expect(
      notificationLink(undefined, 'http://localhost:8000', 'web')
    ).toBeNull();
  });
});

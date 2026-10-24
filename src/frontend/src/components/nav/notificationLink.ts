/** Resolve backend notification URLs against the native frontend base path. */
export function notificationLink(
  link: string | undefined,
  origin: string,
  base: string
): { href: string; route: string | null } | null {
  if (!link) return null;
  const normalizedBase = base.replace(/^\/+|\/+$/g, '');
  const prefix = normalizedBase ? `/${normalizedBase}` : '';
  if (link.startsWith('/') && !link.startsWith('//')) {
    const href =
      link === prefix || link.startsWith(`${prefix}/`)
        ? link
        : `${prefix}${link}`;
    return { href, route: href.slice(prefix.length) || '/' };
  }
  try {
    const url = new URL(link, origin);
    if (
      url.origin === origin &&
      (url.pathname === prefix || url.pathname.startsWith(`${prefix}/`))
    ) {
      const href = `${url.pathname}${url.search}${url.hash}`;
      return { href, route: href.slice(prefix.length) || '/' };
    }
  } catch {
    return { href: link, route: null };
  }
  return { href: link, route: null };
}

import { useCallback, useEffect, useState } from 'react';

/*
 * A very small hash router.
 *
 * The workspace has ten screens and needs shareable, back-button-friendly URLs,
 * but not a routing library: hash routes need no server rewrites (the app is
 * served statically) and add no dependency to install.
 *
 * Routes look like:  #/campaigns/camp_123
 */

const parse = (hash) => {
  const raw = (hash || '').replace(/^#\/?/, '');
  const [pathPart, queryPart] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean);
  const query = Object.fromEntries(new URLSearchParams(queryPart || ''));
  return {
    path: '/' + segments.join('/'),
    segments,
    screen: segments[0] || 'tasks',
    param: segments[1] || null,
    subview: segments[2] || null,
    query
  };
};

export function navigate(path, query) {
  const search = query ? `?${new URLSearchParams(query).toString()}` : '';
  const next = `#${path.startsWith('/') ? path : `/${path}`}${search}`;
  if (window.location.hash === next) return;
  window.location.hash = next;
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  return route;
}

export function useNavigate() {
  return useCallback((path, query) => navigate(path, query), []);
}

import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Pagination,
  Paper,
  Stack,
  Text,
  TextInput,
  Tree,
  type TreeNodeData,
  useTree
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconBox,
  IconBuilding,
  IconBuildingFactory2,
  IconBuildingWarehouse,
  IconChevronRight,
  IconDoor,
  IconLine,
  IconMap2,
  IconMapPin,
  IconSearch
} from '@tabler/icons-react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react';

import { useApi } from '../../../contexts/ApiContext';
import {
  branchHasMore,
  branchPageParams,
  concatBranchPages,
  isNodeValue,
  locationKind,
  missingRevealEdge,
  nodeValue,
  revealExpandTargets,
  revealPaging,
  workspaceLabel
} from './locationTree';
import {
  type LocationNode,
  type PageResult,
  locationApi,
  locationPath
} from './locationTypes';
import classes from './locations.module.css';

export const LOCATION_KIND_ICONS = {
  site: IconBuildingFactory2,
  facility: IconBuildingWarehouse,
  building: IconBuilding,
  area: IconMapPin,
  line: IconLine,
  cell: IconBox,
  room: IconDoor,
  other: IconMap2
} as const;

/** Plain type text for the tree rows and the location header (M1). */
export function locationKindLabel(kind: ReturnType<typeof locationKind>) {
  switch (kind) {
    case 'site':
      return t`Site`;
    case 'facility':
      return t`Facility`;
    case 'building':
      return t`Building`;
    case 'area':
      return t`Area`;
    case 'line':
      return t`Line`;
    case 'cell':
      return t`Cell`;
    case 'room':
      return t`Room`;
    default:
      return t`Other`;
  }
}

interface BranchState {
  rows: LocationNode[];
  hasMore: boolean;
  isError: boolean;
  isFetching: boolean;
  loadedPages: number;
  requestedPages: number;
}

/**
 * Physical location browser (M1/M2): explicit chevrons, type/code/workspace
 * cues, expand and select as separate actions, keyboard support (Enter,
 * Home/End, type-ahead on top of the tree's arrow keys), per-branch "Load
 * more locations" pagination and path reveal for search/deep-link selection
 * beyond the first loaded branch page.
 */
export function LocationBrowser({
  selected,
  onSelect,
  identity,
  workspaces
}: {
  selected?: LocationNode;
  onSelect: (id: number | null) => void;
  identity: number;
  workspaces: { pk: number; name: string }[];
}) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 200);
  const [searchPage, setSearchPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [pages, setPages] = useState<Record<string, number>>({});
  const [revealExhausted, setRevealExhausted] = useState<
    null | 'page-bound' | 'server-exhausted'
  >(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const typeahead = useRef<{ buffer: string; timer: number | null }>({
    buffer: '',
    timer: null
  });

  const parents = useMemo(
    () => ['root', ...Object.keys(expanded).filter((id) => expanded[id])],
    [expanded]
  );

  const branchQueries = useQueries({
    queries: parents.flatMap((parent) => {
      const pageCount = Math.max(1, pages[parent] ?? 1);
      return Array.from({ length: pageCount }, (_, pageIndex) => {
        const params = branchPageParams(parent, pageIndex);
        return {
          queryKey: ['asset-locations', identity, 'branch', parent, pageIndex],
          queryFn: async ({ signal }: { signal: AbortSignal }) =>
            (await api.get(locationApi, { signal, params }))
              .data as PageResult<LocationNode>
        };
      });
    })
  });

  const searchQuery = useQuery<PageResult<LocationNode>>({
    queryKey: ['asset-locations', identity, 'search', debounced, searchPage],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: {
            search: debounced,
            limit: 10,
            offset: (searchPage - 1) * 10
          }
        })
      ).data,
    enabled: !!debounced
  });

  const querySignature = branchQueries
    .map(
      (query) => `${query.dataUpdatedAt}-${query.isError}-${query.isFetching}`
    )
    .join(',');
  const parentsKey = parents.join(',');
  const pagesKey = JSON.stringify(pages);

  const branchesByParent = useMemo(() => {
    const map = new Map<string, BranchState>();
    let index = 0;
    for (const parent of parents) {
      const requestedPages = Math.max(1, pages[parent] ?? 1);
      const queries = branchQueries.slice(index, index + requestedPages);
      index += requestedPages;
      map.set(parent, {
        rows: concatBranchPages(
          queries.map((query) => query.data?.results ?? [])
        ),
        hasMore: branchHasMore(queries.map((query) => query.data)),
        isError: queries.some((query) => query.isError),
        isFetching: queries.some((query) => query.isFetching),
        loadedPages: queries.filter((query) => query.data).length,
        requestedPages
      });
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [querySignature, parentsKey, pagesKey]);

  const nodeLocations = useMemo(() => {
    const map = new Map<string, LocationNode>();
    for (const branch of branchesByParent.values()) {
      for (const row of branch.rows) map.set(nodeValue(row.pk), row);
    }
    return map;
  }, [branchesByParent]);

  const data: TreeNodeData[] = useMemo(() => {
    const build = (parent: string, depth: number): TreeNodeData[] => {
      if (depth > 32) return [];
      const branch = branchesByParent.get(parent);
      return (branch?.rows ?? []).map((node) => ({
        value: nodeValue(node.pk),
        label: node.name,
        hasChildren: node.has_children,
        children: branchesByParent.has(nodeValue(node.pk))
          ? build(nodeValue(node.pk), depth + 1)
          : undefined
      }));
    };
    return build('root', 0);
  }, [branchesByParent]);

  const loadedChildren = useMemo(() => {
    const map: Record<string, number[]> = {};
    for (const [parent, branch] of branchesByParent) {
      map[parent] = branch.rows.map((row) => row.pk);
    }
    return map;
  }, [branchesByParent]);

  const revealPath = selected?.path;
  const revealKey = revealPath?.map((node) => node.pk).join(',');

  // Selection reveal (M2): expand the selected path when the selection
  // changes, and only then. Background query updates never re-expand, so a
  // user can collapse an ancestor and keep the selection without the reveal
  // reopening the branch.
  useEffect(() => {
    if (!revealPath?.length) return;
    const ancestorIds = revealPath
      .slice(0, -1)
      .map((node) => nodeValue(node.pk));
    const targets = revealExpandTargets(ancestorIds, expanded);
    if (!targets.length) return;
    setExpanded((previous) => {
      let changed = false;
      const next = { ...previous };
      for (const id of targets) {
        if (next[id] !== true) {
          next[id] = true;
          changed = true;
        }
      }
      return changed ? next : previous;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealKey]);

  // Background reveal paging: follow the first missing path edge page by
  // page until the selection is visible. Respects the server's continuation
  // flag (never fetches past the end of a branch), the REVEAL_MAX_PAGES
  // bound, and the user's collapse state.
  useEffect(() => {
    if (!revealPath?.length) return;
    const edge = missingRevealEdge(revealPath, loadedChildren);
    if (!edge) {
      setRevealExhausted(null);
      return;
    }
    const decision = revealPaging({
      parent: edge.parent,
      expanded,
      branch: branchesByParent.get(edge.parent),
      currentPages: pages[edge.parent] ?? 1
    });
    if (decision.action === 'page') {
      setPages((previous) => ({ ...previous, [edge.parent]: decision.pages }));
    } else if (decision.action === 'unavailable') {
      setRevealExhausted(decision.reason);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealKey, querySignature, parentsKey, pagesKey]);

  /*
   * The tree controller rebuilds its expanded map from whatever tree data is
   * currently loaded whenever that data changes, dropping keys for nodes that
   * are not in the data yet (their branch is still loading). Replacing state
   * with that map would unmount and refetch those branch queries in a loop,
   * so merge it instead: explicit user toggles land, dropped keys survive.
   */
  const handleExpandedChange = (next: Record<string, boolean>) => {
    setExpanded((previous) => {
      let changed = false;
      const merged = { ...previous };
      for (const [id, value] of Object.entries(next)) {
        if (merged[id] !== value) {
          merged[id] = value;
          changed = true;
        }
      }
      return changed ? merged : previous;
    });
  };

  const tree = useTree({
    expandedState: expanded,
    onExpandedStateChange: handleExpandedChange,
    selectedState: selected ? [String(selected.pk)] : [],
    onSelectedStateChange: (ids) => {
      if (isNodeValue(ids[0])) onSelect(Number(ids[0]));
    }
  });

  const selectValue = (value: string | null | undefined) => {
    if (isNodeValue(value)) onSelect(Number(value));
  };

  const focusItem = (item: Element | null | undefined) => {
    if (!item) return;
    item.setAttribute('data-focus-ring', 'true');
    (item as HTMLElement).focus();
  };

  const visibleItems = () =>
    Array.from(
      containerRef.current?.querySelectorAll('[role="treeitem"]') ?? []
    );

  /*
   * Application-level keyboard adapter (M1): the installed Tree provides
   * arrow navigation and space-to-expand but no Enter selection, Home/End or
   * type-ahead, and those keys cannot be attached through its public props.
   * Key events bubble from the treeitems to this wrapper. Keys handled by a
   * focused control (chevron, label button) keep their native semantics.
   */
  const handleTreeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    const item = target.closest('[role="treeitem"]');
    if (!item || target !== item) return;
    const items = visibleItems();
    const index = items.indexOf(item);
    if (event.key === 'Enter') {
      event.preventDefault();
      selectValue(item.getAttribute('data-value'));
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      focusItem(event.key === 'Home' ? items[0] : items[items.length - 1]);
      return;
    }
    if (
      event.key.length === 1 &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
      event.preventDefault();
      const state = typeahead.current;
      if (state.timer) window.clearTimeout(state.timer);
      state.buffer += event.key.toLowerCase();
      state.timer = window.setTimeout(() => {
        state.buffer = '';
        state.timer = null;
      }, 600);
      const matches = (candidate: Element) =>
        (candidate.textContent ?? '')
          .trim()
          .toLowerCase()
          .startsWith(state.buffer);
      const ordered = [...items.slice(index + 1), ...items.slice(0, index + 1)];
      focusItem(ordered.find(matches) ?? items.find(matches));
    }
  };

  const renderNode = ({
    node,
    expanded: isExpanded,
    hasChildren,
    isRoot,
    tree: controller,
    elementProps
  }: Tree.RenderNodePayload) => {
    const location = nodeLocations.get(node.value);
    const kind = locationKind(location?.kind);
    const KindIcon = LOCATION_KIND_ICONS[kind];
    const label = location?.name ?? String(node.label ?? '');
    const workspace = workspaceLabel(location?.client ?? 0, workspaces);
    return (
      <Group
        {...elementProps}
        className={classes.nodeLabel}
        wrap='nowrap'
        gap='xs'
        onClick={(event) => {
          // Keeps the treeitem focused (library behaviour) and selects.
          elementProps.onClick(event);
          selectValue(node.value);
        }}
      >
        {hasChildren ? (
          <ActionIcon
            className={classes.chevron}
            data-expanded={isExpanded || undefined}
            variant='transparent'
            size='sm'
            aria-expanded={isExpanded}
            aria-label={`${isExpanded ? t`Collapse` : t`Expand`} ${label}`}
            onClick={(event) => {
              event.stopPropagation();
              controller.toggleExpanded(node.value);
            }}
          >
            <IconChevronRight size={16} />
          </ActionIcon>
        ) : (
          <Box w={26} />
        )}
        <KindIcon size={16} aria-hidden />
        <button type='button' className={classes.nodeName}>
          {label}
        </button>
        <Text size='xs' c='dimmed'>
          {locationKindLabel(kind)}
        </Text>
        {location?.code ? (
          <Text size='xs' c='dimmed'>
            {location.code}
          </Text>
        ) : null}
        {location?.archived && (
          <Badge size='xs' color='gray' variant='light'>
            {t`Archived`}
          </Badge>
        )}
        {isRoot && workspace && (
          <Text size='xs' c='dimmed'>
            {workspace}
          </Text>
        )}
      </Group>
    );
  };

  const continuations = parents
    .map((parent) => ({
      parent,
      name:
        parent === 'root'
          ? t`Top-level locations`
          : (nodeLocations.get(parent)?.name ?? parent),
      state: branchesByParent.get(parent)
    }))
    .filter((entry) => entry.state?.hasMore);

  return (
    <Paper withBorder p='md'>
      <Stack gap='sm'>
        <Group gap='xs'>
          <IconBuildingFactory2 size={20} />
          <Text fw={600}>{t`Locations`}</Text>
        </Group>
        <TextInput
          aria-label={t`Search locations`}
          placeholder={t`Search names or codes`}
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            setSearchPage(1);
          }}
        />
        {debounced ? (
          <>
            {searchQuery.isFetching && <Loader size='sm' />}
            {searchQuery.isError && (
              <Alert color='red'>{t`Locations could not be loaded.`}</Alert>
            )}
            {!searchQuery.isError &&
              searchQuery.data?.results.map((node) => (
                <Anchor
                  key={node.pk}
                  component='button'
                  ta='left'
                  onClick={() => {
                    selectValue(nodeValue(node.pk));
                    setSearch('');
                  }}
                >
                  {locationPath(node)}
                </Anchor>
              ))}
            {searchQuery.data?.count === 0 && (
              <Text c='dimmed' size='sm'>{t`No matching locations`}</Text>
            )}
            {(searchQuery.data?.count ?? 0) > 10 && (
              <Pagination
                total={Math.ceil((searchQuery.data?.count ?? 0) / 10)}
                value={searchPage}
                onChange={setSearchPage}
                size='xs'
              />
            )}
          </>
        ) : (
          <>
            <Anchor
              component='button'
              ta='left'
              onClick={() => onSelect(null)}
            >{t`Browse top-level locations`}</Anchor>
            {branchesByParent.get('root')?.isFetching && <Loader size='sm' />}
            {[...branchesByParent.entries()]
              .filter(([, branch]) => branch.isError)
              .map(([parent]) => (
                <Alert
                  key={parent}
                  color='red'
                  title={t`Locations unavailable`}
                >
                  {t`This branch could not be loaded.`}
                  <Button
                    size='xs'
                    variant='light'
                    onClick={() =>
                      queryClient.invalidateQueries({
                        queryKey: [
                          'asset-locations',
                          identity,
                          'branch',
                          parent
                        ]
                      })
                    }
                  >{t`Retry`}</Button>
                </Alert>
              ))}
            <div ref={containerRef} onKeyDown={handleTreeKeyDown}>
              <Tree
                data={data}
                tree={tree}
                expandOnClick={false}
                selectOnClick={false}
                allowRangeSelection={false}
                expandOnSpace
                withLines
                aria-label={t`Location tree`}
                renderNode={renderNode}
              />
            </div>
            {continuations.map((entry) => (
              <Group key={entry.parent} justify='space-between' gap='xs'>
                <Text size='xs' c='dimmed'>
                  {entry.name}
                </Text>
                <Button
                  size='xs'
                  variant='light'
                  onClick={() =>
                    setPages((previous) => ({
                      ...previous,
                      [entry.parent]: (previous[entry.parent] ?? 1) + 1
                    }))
                  }
                >{t`Load more locations`}</Button>
              </Group>
            ))}
            {revealExhausted === 'page-bound' && (
              <Alert color='yellow'>
                {t`This location is beyond the loaded tree pages. Use the search above to reach it.`}
              </Alert>
            )}
            {revealExhausted === 'server-exhausted' && (
              <Alert color='yellow'>
                {t`This location cannot be found in the tree. Use the search above to look for it.`}
              </Alert>
            )}
            {branchesByParent.get('root')?.rows.length === 0 &&
              !branchesByParent.get('root')?.isFetching &&
              !branchesByParent.get('root')?.isError && (
                <Text
                  c='dimmed'
                  size='sm'
                >{t`Create your first site to organize machines.`}</Text>
              )}
          </>
        )}
      </Stack>
    </Paper>
  );
}

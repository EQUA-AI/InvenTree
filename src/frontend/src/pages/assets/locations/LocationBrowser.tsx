import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Anchor,
  Box,
  Button,
  Checkbox,
  Group,
  Loader,
  Pagination,
  Paper,
  Stack,
  Text,
  TextInput,
  Tooltip,
  Tree,
  type TreeNodeData,
  useMatches,
  useTree
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconArchive,
  IconArrowLeft,
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
import operations from '../operations.module.css';

import { factTooltipProps } from '../../../components/items/ValueTooltip';
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
  workspaces,
  showArchived = false,
  onShowArchived
}: {
  showArchived?: boolean;
  onShowArchived?: (show: boolean) => void;
  selected?: LocationNode;
  onSelect: (id: number | null) => void;
  identity: number;
  workspaces: { pk: number; name: string }[];
}) {
  const api = useApi();
  const phone = useMatches({ base: true, sm: false });
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [focusedNode, setFocusedNode] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [debounced] = useDebouncedValue(search, 200);
  const [searchPage, setSearchPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [pages, setPages] = useState<Record<string, number>>({});
  const [drillParent, setDrillParent] = useState('root');
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
        const params = {
          ...branchPageParams(parent, pageIndex),
          active_only: !showArchived
        };
        return {
          queryKey: [
            'asset-locations',
            identity,
            'branch',
            parent,
            showArchived,
            pageIndex
          ],
          queryFn: async ({ signal }: { signal: AbortSignal }) =>
            (await api.get(locationApi, { signal, params }))
              .data as PageResult<LocationNode>
        };
      });
    })
  });

  const searchQuery = useQuery<PageResult<LocationNode>>({
    queryKey: [
      'asset-locations',
      identity,
      'search',
      debounced,
      showArchived,
      searchPage
    ],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: {
            active_only: !showArchived,
            search: debounced,
            limit: 10,
            offset: (searchPage - 1) * 10
          }
        })
      ).data,
    enabled: !!debounced
  });

  const querySignature = [
    showArchived,
    ...branchQueries.map(
      (query) => `${query.dataUpdatedAt}-${query.isError}-${query.isFetching}`
    )
  ].join(',');
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
  useEffect(() => {
    if (phone && selected)
      setDrillParent(
        selected.parent == null ? 'root' : nodeValue(selected.parent)
      );
  }, [phone, selected?.pk, selected?.parent]);

  const openBranch = (node: LocationNode) => {
    const value = nodeValue(node.pk);
    setExpanded((previous) => ({ ...previous, [value]: true }));
    setDrillParent(value);
    setFocusedNode(null);
    setHoveredNode(null);
  };
  const nodeDetail = (node?: LocationNode) =>
    [
      node?.name,
      node?.code,
      node?.kind && locationKindLabel(locationKind(node.kind)),
      node?.archived && t`Archived`,
      workspaces.length > 1 && workspaceLabel(node?.client ?? 0, workspaces)
    ]
      .filter(Boolean)
      .join(' · ');

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
    if (event.key === 'Escape') {
      setFocusedNode(null);
      setHoveredNode(null);
      return;
    }
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
    level,
    tree: controller,
    elementProps
  }: Tree.RenderNodePayload) => {
    const location = nodeLocations.get(node.value);
    const kind = locationKind(location?.kind);
    const KindIcon = LOCATION_KIND_ICONS[kind];
    const label = location?.name ?? String(node.label ?? '');
    return (
      <Tooltip
        label={nodeDetail(location) || label}
        {...factTooltipProps}
        opened={focusedNode === node.value || hoveredNode === node.value}
      >
        <Box
          {...elementProps}
          onMouseEnter={() => setHoveredNode(node.value)}
          onMouseLeave={() => setHoveredNode(null)}
          className={`${elementProps.className} ${classes.nodeLabel}`}
          data-depth={level}
          onClick={(event) => {
            // Keeps the treeitem focused (library behaviour) and selects.
            elementProps.onClick(event);
            selectValue(node.value);
          }}
        >
          {hasChildren ? (
            <Box
              component='span'
              className={classes.chevron}
              data-expanded={isExpanded || undefined}
              aria-hidden='true'
              onClick={(event) => {
                event.stopPropagation();
                controller.toggleExpanded(node.value);
              }}
            >
              <IconChevronRight size={16} />
            </Box>
          ) : (
            <Box w={32} />
          )}
          <KindIcon size={16} aria-hidden />
          <span className={classes.nodeName}>
            {label}
            <span className={classes.treeDetail}>
              {location?.code ? ` · ${location.code}` : ''}
            </span>
          </span>
          {location?.archived && (
            <IconArchive size={16} aria-label={t`Archived`} />
          )}
        </Box>
      </Tooltip>
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
          placeholder={t`Name or code`}
          classNames={{ input: operations.input }}
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            setSearchPage(1);
          }}
        />
        {onShowArchived && (
          <Checkbox
            label={t`Show archived locations`}
            styles={{
              body: { alignItems: 'center' },
              label: {
                minHeight: phone ? 44 : undefined,
                display: 'flex',
                alignItems: 'center'
              }
            }}
            checked={showArchived}
            onChange={(event) => {
              setSearchPage(1);
              setPages({});
              onShowArchived(event.currentTarget.checked);
            }}
          />
        )}
        {debounced ? (
          <>
            {searchQuery.isFetching && <Loader size='sm' />}
            {searchQuery.isError && (
              <Alert color='red'>{t`Locations could not be loaded.`}</Alert>
            )}
            {!searchQuery.isError &&
              searchQuery.data?.results.map((node) => (
                <Anchor
                  className={operations.link}
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
              <Text
                className={operations.meta}
                size='sm'
              >{t`No matching locations`}</Text>
            )}
            {(searchQuery.data?.count ?? 0) > 10 && (
              <Pagination
                color='var(--mantine-primary-color-7)'
                total={Math.ceil((searchQuery.data?.count ?? 0) / 10)}
                value={searchPage}
                onChange={setSearchPage}
                size='xs'
              />
            )}
          </>
        ) : (
          <>
            {(selected || (phone && drillParent !== 'root')) && (
              <Anchor
                className={operations.link}
                component='button'
                ta='left'
                onClick={() => {
                  setDrillParent('root');
                  onSelect(null);
                }}
              >{t`Browse top-level locations`}</Anchor>
            )}
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
            {phone ? (
              <Stack
                gap={4}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    setFocusedNode(null);
                    setHoveredNode(null);
                  }
                }}
              >
                {drillParent !== 'root' && (
                  <Group gap='xs' wrap='nowrap'>
                    <ActionIcon
                      variant='default'
                      size={44}
                      aria-label={t`Back to parent location`}
                      onClick={() => {
                        const parent = nodeLocations.get(drillParent)?.parent;
                        setDrillParent(
                          parent == null ? 'root' : nodeValue(parent)
                        );
                      }}
                    >
                      <IconArrowLeft size={18} />
                    </ActionIcon>
                    <Anchor
                      component='button'
                      ta='left'
                      className={`${classes.nodeName} ${classes.drillParent}`}
                      aria-label={t`View ${nodeLocations.get(drillParent)?.name ?? ''}`}
                      onClick={() => selectValue(drillParent)}
                    >
                      {nodeLocations.get(drillParent)?.name}
                    </Anchor>
                  </Group>
                )}
                {branchesByParent.get(drillParent)?.isFetching && (
                  <Loader size='sm' />
                )}
                <ul
                  className={classes.drillList}
                  aria-label={t`Location drill-down`}
                >
                  {branchesByParent.get(drillParent)?.rows.map((node) => {
                    const Icon = LOCATION_KIND_ICONS[locationKind(node.kind)];
                    return (
                      <li key={node.pk}>
                        <Tooltip
                          label={nodeDetail(node)}
                          opened={
                            focusedNode === nodeValue(node.pk) ||
                            hoveredNode === nodeValue(node.pk)
                          }
                          {...factTooltipProps}
                          events={{ hover: true, focus: true, touch: false }}
                        >
                          <button
                            type='button'
                            className={classes.drillRow}
                            onFocus={() => setFocusedNode(nodeValue(node.pk))}
                            onBlur={() => setFocusedNode(null)}
                            onMouseEnter={() =>
                              setHoveredNode(nodeValue(node.pk))
                            }
                            onMouseLeave={() => setHoveredNode(null)}
                            aria-current={
                              selected?.pk === node.pk ? 'location' : undefined
                            }
                            onClick={() =>
                              node.has_children
                                ? openBranch(node)
                                : onSelect(node.pk)
                            }
                          >
                            <Icon size={18} aria-hidden />
                            <span>{node.name}</span>
                            {node.has_children && (
                              <IconChevronRight size={18} aria-hidden />
                            )}
                          </button>
                        </Tooltip>
                      </li>
                    );
                  })}
                </ul>
              </Stack>
            ) : (
              <div
                ref={containerRef}
                className={classes.treeViewport}
                onKeyDown={handleTreeKeyDown}
                onFocusCapture={(event) =>
                  setFocusedNode(
                    (event.target as HTMLElement)
                      .closest('[role="treeitem"]')
                      ?.getAttribute('data-value') ?? null
                  )
                }
                onBlurCapture={() => setFocusedNode(null)}
              >
                <Tree
                  data={data}
                  tree={tree}
                  expandOnClick={false}
                  selectOnClick={false}
                  allowRangeSelection={false}
                  expandOnSpace
                  levelOffset={24}
                  aria-label={t`Location tree`}
                  renderNode={renderNode}
                />
              </div>
            )}
            {continuations
              .filter((entry) => !phone || entry.parent === drillParent)
              .map((entry) => (
                <Group key={entry.parent} justify='space-between' gap='xs'>
                  <Text size='xs' className={operations.meta}>
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
                  className={operations.meta}
                  size='sm'
                >{t`Create your first site to organize machines.`}</Text>
              )}
          </>
        )}
      </Stack>
    </Paper>
  );
}

"""Transactional placement capture; no actor grant or remote execution occurs."""

INSERT_TRIGGER = 'integration_state_placement_insert_v1'
UPDATE_TRIGGER = 'integration_state_placement_update_v1'
DELETE_TRIGGER = 'integration_state_placement_delete_v1'
CAPTURE_FUNCTION = 'integration_state_capture_placement_v1'


def capture_statements(vendor):
    """Return fixed version-one SQL, never identifiers supplied by a request."""
    if vendor == 'postgresql':
        return _postgres_statements()
    if vendor != 'sqlite':
        raise ValueError('Placement capture is not qualified for this database')
    return (
        f"""
CREATE TRIGGER {INSERT_TRIGGER}
AFTER INSERT ON assets_assetmachine
WHEN EXISTS (SELECT 1 FROM integration_state_sourcenamespace WHERE active = 1)
BEGIN
    INSERT INTO integration_state_sourcestate
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         client_id, location_id, native_version, deleted)
    SELECT id, epoch, 'machine', 'placement', NEW.id, 1,
           NEW.client_id, NEW.physical_location_id, NEW.placement_version, 0
    FROM integration_state_sourcenamespace WHERE active = 1
    ON CONFLICT(namespace_id, epoch, entity_kind, aspect, source_id)
    DO UPDATE SET revision = integration_state_sourcestate.revision + 1,
                  client_id = excluded.client_id,
                  location_id = excluded.location_id,
                  native_version = excluded.native_version,
                  deleted = 0;
    INSERT INTO integration_state_sourcechange
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         operation, previous, current, captured_at)
    SELECT state.namespace_id, state.epoch, 'machine', 'placement', NEW.id,
           state.revision, 'created', NULL,
           json_object('client_id', NEW.client_id,
                       'location_id', NEW.physical_location_id,
                       'native_version', NEW.placement_version),
           CURRENT_TIMESTAMP
    FROM integration_state_sourcestate AS state
    JOIN integration_state_sourcenamespace AS namespace
      ON namespace.id = state.namespace_id AND namespace.epoch = state.epoch
    WHERE namespace.active = 1 AND state.entity_kind = 'machine'
      AND state.aspect = 'placement' AND state.source_id = NEW.id;
END
""".strip(),
        f"""
CREATE TRIGGER {UPDATE_TRIGGER}
AFTER UPDATE OF client_id, physical_location_id, placement_version
ON assets_assetmachine
WHEN (
    NEW.client_id IS NOT OLD.client_id
    OR NEW.physical_location_id IS NOT OLD.physical_location_id
    OR NEW.placement_version IS NOT OLD.placement_version
) AND EXISTS (SELECT 1 FROM integration_state_sourcenamespace WHERE active = 1)
BEGIN
    INSERT INTO integration_state_sourcestate
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         client_id, location_id, native_version, deleted)
    SELECT id, epoch, 'machine', 'placement', NEW.id, 1,
           NEW.client_id, NEW.physical_location_id, NEW.placement_version, 0
    FROM integration_state_sourcenamespace WHERE active = 1
    ON CONFLICT(namespace_id, epoch, entity_kind, aspect, source_id)
    DO UPDATE SET revision = integration_state_sourcestate.revision + 1,
                  client_id = excluded.client_id,
                  location_id = excluded.location_id,
                  native_version = excluded.native_version,
                  deleted = 0;
    INSERT INTO integration_state_sourcechange
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         operation, previous, current, captured_at)
    SELECT state.namespace_id, state.epoch, 'machine', 'placement', NEW.id,
           state.revision, 'changed',
           json_object('client_id', OLD.client_id,
                       'location_id', OLD.physical_location_id,
                       'native_version', OLD.placement_version),
           json_object('client_id', NEW.client_id,
                       'location_id', NEW.physical_location_id,
                       'native_version', NEW.placement_version),
           CURRENT_TIMESTAMP
    FROM integration_state_sourcestate AS state
    JOIN integration_state_sourcenamespace AS namespace
      ON namespace.id = state.namespace_id AND namespace.epoch = state.epoch
    WHERE namespace.active = 1 AND state.entity_kind = 'machine'
      AND state.aspect = 'placement' AND state.source_id = NEW.id;
END
""".strip(),
        f"""
CREATE TRIGGER {DELETE_TRIGGER}
AFTER DELETE ON assets_assetmachine
WHEN EXISTS (SELECT 1 FROM integration_state_sourcenamespace WHERE active = 1)
BEGIN
    INSERT INTO integration_state_sourcestate
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         client_id, location_id, native_version, deleted)
    SELECT id, epoch, 'machine', 'placement', OLD.id, 1,
           OLD.client_id, OLD.physical_location_id, OLD.placement_version, 1
    FROM integration_state_sourcenamespace WHERE active = 1
    ON CONFLICT(namespace_id, epoch, entity_kind, aspect, source_id)
    DO UPDATE SET revision = integration_state_sourcestate.revision + 1,
                  client_id = excluded.client_id,
                  location_id = excluded.location_id,
                  native_version = excluded.native_version,
                  deleted = 1;
    INSERT INTO integration_state_sourcechange
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         operation, previous, current, captured_at)
    SELECT state.namespace_id, state.epoch, 'machine', 'placement', OLD.id,
           state.revision, 'deleted',
           json_object('client_id', OLD.client_id,
                       'location_id', OLD.physical_location_id,
                       'native_version', OLD.placement_version),
           NULL, CURRENT_TIMESTAMP
    FROM integration_state_sourcestate AS state
    JOIN integration_state_sourcenamespace AS namespace
      ON namespace.id = state.namespace_id AND namespace.epoch = state.epoch
    WHERE namespace.active = 1 AND state.entity_kind = 'machine'
      AND state.aspect = 'placement' AND state.source_id = OLD.id;
END
""".strip(),
    )


def _postgres_statements():
    """Use the writer's own privileges and a fixed, qualified PostgreSQL path."""
    function = f"""
CREATE FUNCTION public.{CAPTURE_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $capture$
DECLARE
    v_namespace bigint;
    v_epoch uuid;
    v_source_id bigint;
    v_client_id bigint;
    v_location_id bigint;
    v_native_version bigint;
    v_revision bigint;
    v_deleted boolean;
    v_previous jsonb;
    v_current jsonb;
    v_operation text;
BEGIN
    IF TG_OP = 'UPDATE' AND
       ROW(OLD.client_id, OLD.physical_location_id, OLD.placement_version)
       IS NOT DISTINCT FROM
       ROW(NEW.client_id, NEW.physical_location_id, NEW.placement_version) THEN
        RETURN NULL;
    END IF;
    SELECT id, epoch INTO v_namespace, v_epoch
    FROM public.integration_state_sourcenamespace WHERE active;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'DELETE' THEN
        v_source_id := OLD.id;
        v_client_id := OLD.client_id;
        v_location_id := OLD.physical_location_id;
        v_native_version := OLD.placement_version;
        v_deleted := TRUE;
        v_operation := 'deleted';
    ELSE
        v_source_id := NEW.id;
        v_client_id := NEW.client_id;
        v_location_id := NEW.physical_location_id;
        v_native_version := NEW.placement_version;
        v_deleted := FALSE;
        v_operation := CASE WHEN TG_OP = 'INSERT' THEN 'created' ELSE 'changed' END;
    END IF;
    IF TG_OP != 'INSERT' THEN
        v_previous := jsonb_build_object(
            'client_id', OLD.client_id, 'location_id', OLD.physical_location_id,
            'native_version', OLD.placement_version);
    END IF;
    IF TG_OP != 'DELETE' THEN
        v_current := jsonb_build_object(
            'client_id', NEW.client_id, 'location_id', NEW.physical_location_id,
            'native_version', NEW.placement_version);
    END IF;
    INSERT INTO public.integration_state_sourcestate
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         client_id, location_id, native_version, deleted)
    VALUES (v_namespace, v_epoch, 'machine', 'placement', v_source_id, 1,
            v_client_id, v_location_id, v_native_version, v_deleted)
    ON CONFLICT(namespace_id, epoch, entity_kind, aspect, source_id)
    DO UPDATE SET revision = public.integration_state_sourcestate.revision + 1,
                  client_id = EXCLUDED.client_id,
                  location_id = EXCLUDED.location_id,
                  native_version = EXCLUDED.native_version,
                  deleted = EXCLUDED.deleted
    RETURNING revision INTO v_revision;
    INSERT INTO public.integration_state_sourcechange
        (namespace_id, epoch, entity_kind, aspect, source_id, revision,
         operation, previous, current, captured_at)
    VALUES (v_namespace, v_epoch, 'machine', 'placement', v_source_id, v_revision,
            v_operation, v_previous, v_current, CURRENT_TIMESTAMP);
    RETURN NULL;
END;
$capture$
""".strip()
    return (
        function,
        f'CREATE TRIGGER {INSERT_TRIGGER} AFTER INSERT ON public.assets_assetmachine '
        f'FOR EACH ROW EXECUTE FUNCTION public.{CAPTURE_FUNCTION}()',
        f'CREATE TRIGGER {UPDATE_TRIGGER} AFTER UPDATE OF client_id, '
        'physical_location_id, placement_version ON public.assets_assetmachine '
        f'FOR EACH ROW EXECUTE FUNCTION public.{CAPTURE_FUNCTION}()',
        f'CREATE TRIGGER {DELETE_TRIGGER} AFTER DELETE ON public.assets_assetmachine '
        f'FOR EACH ROW EXECUTE FUNCTION public.{CAPTURE_FUNCTION}()',
    )


def _require_qualified_schema(connection, cursor):
    """Never operate on public objects for a different selected source schema."""
    if connection.vendor == 'postgresql':
        cursor.execute('SELECT current_schema()')
        if cursor.fetchone() != ('public',):
            raise ValueError('Placement capture requires the qualified public schema')


def capture_is_installed(connection):
    """Inspect the actual selected database, not a process-local ready flag."""
    statements = capture_statements(connection.vendor)
    names = (INSERT_TRIGGER, UPDATE_TRIGGER, DELETE_TRIGGER)
    with connection.cursor() as cursor:
        _require_qualified_schema(connection, cursor)
        if connection.vendor == 'sqlite':
            cursor.execute(
                'SELECT name, sql FROM sqlite_master WHERE type=%s AND tbl_name=%s '
                'AND name IN (%s, %s, %s)',
                ['trigger', 'assets_assetmachine', *names],
            )
            actual = dict(cursor.fetchall())
            return actual == {
                name: statement.strip()
                for name, statement in zip(names, statements, strict=True)
            }
        return _postgres_capture_verified(cursor, names, statements[0])


def _postgres_capture_verified(cursor, names, function):
    """Bind enabled trigger events/columns and exact invoker function semantics."""
    cursor.execute(
        'SELECT attname, attnum FROM pg_attribute '
        "WHERE attrelid='public.assets_assetmachine'::regclass "
        'AND NOT attisdropped AND attname IN (%s, %s, %s)',
        ['client_id', 'physical_location_id', 'placement_version'],
    )
    attributes = dict(cursor.fetchall())
    columns = ('client_id', 'physical_location_id', 'placement_version')
    if set(attributes) != set(columns):
        return False
    cursor.execute(
        'SELECT t.tgname, t.tgtype, t.tgenabled, t.tgattr::text, '
        'p.prosrc, p.proconfig, '
        '(NOT t.tgisinternal AND t.tgnargs=0 AND t.tgconstraint=0 '
        'AND t.tgqual IS NULL AND t.tgoldtable IS NULL AND t.tgnewtable IS NULL '
        'AND NOT t.tgdeferrable AND NOT t.tginitdeferred '
        "AND p.proname=%s AND n.nspname='public' AND p.pronargs=0 "
        "AND p.prorettype='pg_catalog.trigger'::regtype AND l.lanname='plpgsql' "
        "AND p.prokind='f' AND p.provolatile='v' "
        'AND NOT p.prosecdef AND NOT p.proleakproof) '
        'FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid '
        'JOIN pg_namespace n ON n.oid=p.pronamespace '
        'JOIN pg_language l ON l.oid=p.prolang '
        "WHERE t.tgrelid='public.assets_assetmachine'::regclass "
        'AND t.tgname IN (%s, %s, %s)',
        [CAPTURE_FUNCTION, *names],
    )
    rows = cursor.fetchall()
    if len(rows) != 3 or {row[0] for row in rows} != set(names):
        return False
    kinds = {INSERT_TRIGGER: 5, UPDATE_TRIGGER: 17, DELETE_TRIGGER: 9}
    body = function.split('$capture$')[1]
    for name, kind, enabled, watched, source, config, qualified in rows:
        expected_columns = (
            tuple(attributes[column] for column in columns)
            if name == UPDATE_TRIGGER
            else ()
        )
        if (
            kind != kinds[name]
            or enabled != 'O'
            or tuple(int(value) for value in watched.split()) != expected_columns
            or source != body
            or config != ['search_path=pg_catalog, public']
            or qualified is not True
        ):
            return False
    return True


def install_capture(connection):
    """Install on an explicitly selected database after its real tables exist."""
    statements = capture_statements(connection.vendor)
    with connection.cursor() as cursor:
        _require_qualified_schema(connection, cursor)
        for statement in statements:
            cursor.execute(statement)


def uninstall_capture(connection):
    """Remove fixed owned objects; migrations must first guard retained state."""
    capture_statements(connection.vendor)
    with connection.cursor() as cursor:
        _require_qualified_schema(connection, cursor)
        for name in (DELETE_TRIGGER, UPDATE_TRIGGER, INSERT_TRIGGER):
            suffix = (
                ' ON public.assets_assetmachine'
                if connection.vendor == 'postgresql'
                else ''
            )
            cursor.execute(f'DROP TRIGGER {name}{suffix}')
        if connection.vendor == 'postgresql':
            cursor.execute(f'DROP FUNCTION public.{CAPTURE_FUNCTION}()')

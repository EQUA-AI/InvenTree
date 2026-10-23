"""Checkpoint operations: catalog verification and the replaced batch's model state."""

from django.db.migrations.operations.base import Operation

from legacy_boundary.checkpoint import verify_checkpoint


class VerifyCheckpoint(Operation):
    """Check the physical boundary without changing Django model state."""

    reduces_to_sql = False
    reversible = True

    def state_forwards(self, app_label, state):
        """The checkpoint does not change the active application's models."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Read the catalog without materializing historical model registries."""
        verify_checkpoint(None, schema_editor)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Reversing verification changes neither storage nor model state."""

    def describe(self):
        """Describe this step in migration plans and collected SQL comments."""
        return 'Verify the observed schema checkpoint and persistent focus defaults'


class ReplayedState(Operation):
    """Carry the replaced batch's model state; storage comes from the checkpoint.

    Django drops replaced migrations from the graph, so without this the
    models those 17 migrations introduced never enter migration state: fresh
    databases get no content types or permissions for them, ``makemigrations``
    proposes recreating them, and non-PostgreSQL installs lack their tables.
    PostgreSQL storage is created by the checkpoint SQL; other engines build
    it from the same schema operations. Data steps are never replayed, as on
    PostgreSQL.
    """

    reduces_to_sql = False
    reversible = False

    def __init__(self, replaced):
        """Name the replaced migrations whose schema state is carried."""
        self.replaced = tuple(tuple(key) for key in replaced)

    def deconstruct(self):
        """Serialise as the same replaced-migration list."""
        return (self.__class__.__name__, [list(self.replaced)], {})

    def schema_operations(self):
        """Yield (app_label, operation) in dependency order, skipping data steps."""
        from importlib import import_module

        from django.db.migrations.operations import RunPython, RunSQL

        loaded = {
            key: import_module(f'{key[0]}.migrations.{key[1]}').Migration
            for key in self.replaced
        }
        done, ordered = set(), []
        while len(ordered) < len(loaded):
            ready = [
                key
                for key in self.replaced
                if key not in done
                and all(
                    tuple(parent) in done or tuple(parent) not in loaded
                    for parent in loaded[key].dependencies
                )
            ]
            if not ready:
                raise ValueError('Replaced migrations have a dependency cycle')
            done.add(ready[0])
            ordered.append(ready[0])
        for key in ordered:
            for operation in loaded[key].operations:
                if not isinstance(operation, (RunPython, RunSQL)):
                    yield key[0], operation

    def state_forwards(self, app_label, state):
        """Apply each replaced schema operation to its own app's state."""
        for label, operation in self.schema_operations():
            operation.state_forwards(label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Create the storage on engines the checkpoint SQL does not cover."""
        if schema_editor.connection.vendor == 'postgresql':
            return
        state = from_state
        for label, operation in self.schema_operations():
            new_state = state.clone()
            operation.state_forwards(label, new_state)
            operation.database_forwards(label, schema_editor, state, new_state)
            state = new_state

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """The checkpoint is irreversible; see the migration."""
        raise NotImplementedError('The legacy checkpoint cannot be reversed')

    def describe(self):
        """Describe this step in migration plans."""
        return 'Carry the model state of the replaced 2026-10-21 batch'

"""SQLite dark-schema compatibility; semantic execution still requires PostgreSQL.

PostgreSQL keeps native arrays, array checks, GIN and HNSW. SQLite fixtures keep
JSON-encoded topics and ordinary indexes so Django's FK collector remains valid.
Those fixtures cannot qualify vector recall or PostgreSQL array constraints.
"""

import json

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from pgvector.django import HnswIndex


class MemoryTopicsField(ArrayField):
    """Native PostgreSQL array with an explicit SQLite serialization adapter."""

    def db_type(self, connection):
        """SQLite needs a real column for deletion-collector compatibility."""
        return 'text' if connection.vendor == 'sqlite' else super().db_type(connection)

    def get_placeholder(self, value, compiler, connection):
        """Only PostgreSQL accepts the native array cast."""
        if connection.vendor == 'sqlite':
            return '%s'
        return super().get_placeholder(value, compiler, connection)

    def get_db_prep_value(self, value, connection, prepared=False):
        """Serialize fixture arrays without changing PostgreSQL adaptation."""
        if connection.vendor == 'sqlite':
            return json.dumps(value) if value is not None else None
        return super().get_db_prep_value(value, connection, prepared)

    def from_db_value(self, value, expression, connection):
        """PostgreSQL drivers already return an array; SQLite returns JSON text."""
        if connection.vendor == 'sqlite' and value is not None:
            return json.loads(value)
        return value


class MemoryTopicsConstraint(models.CheckConstraint):
    """Array operators are qualified on PostgreSQL only."""

    def constraint_sql(self, model, schema_editor):
        """Keep unsupported array syntax out of SQLite fixture DDL."""
        if schema_editor.connection.vendor != 'postgresql':
            return None
        return super().constraint_sql(model, schema_editor)

    def create_sql(self, model, schema_editor):
        """Apply the same boundary when adding the constraint separately."""
        if schema_editor.connection.vendor != 'postgresql':
            return None
        return super().create_sql(model, schema_editor)

    def remove_sql(self, model, schema_editor):
        """No non-PostgreSQL array constraint exists to remove."""
        if schema_editor.connection.vendor != 'postgresql':
            return None
        return super().remove_sql(model, schema_editor)

    def validate(self, model, instance, exclude=None, using='default'):
        """Do not emulate PostgreSQL constraint acceptance on SQLite."""
        from django.db import connections

        if connections[using].vendor == 'postgresql':
            return super().validate(model, instance, exclude=exclude, using=using)
        return None


class MemoryGinIndex(GinIndex):
    """GIN on PostgreSQL; ordinary fixture index on SQLite."""

    def create_sql(self, model, schema_editor, using='', **kwargs):
        """Fixture indexes have no PostgreSQL operator classes or parameters."""
        if schema_editor.connection.vendor == 'sqlite':
            return models.Index(fields=self.fields, name=self.name).create_sql(
                model, schema_editor
            )
        return super().create_sql(model, schema_editor, using=using, **kwargs)


class MemoryHnswIndex(HnswIndex):
    """HNSW on PostgreSQL; ordinary fixture index on SQLite."""

    def create_sql(self, model, schema_editor, using='', **kwargs):
        """A SQLite index does not implement or qualify semantic search."""
        if schema_editor.connection.vendor == 'sqlite':
            return models.Index(fields=self.fields, name=self.name).create_sql(
                model, schema_editor
            )
        return super().create_sql(model, schema_editor, using=using, **kwargs)

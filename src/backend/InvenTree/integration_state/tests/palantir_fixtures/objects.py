"""Property descriptors matching the synthetic generated-SDK facade."""

from types import SimpleNamespace


class Machine:
    """A synthetic generated class, independent of any real ontology."""

    object_type = SimpleNamespace(
        native_pk='native_pk',
        client_pk='client_pk',
        client_birth='client_birth',
        incarnation='incarnation',
        namespace='namespace',
        display_name='display_name',
    )


class Location(Machine):
    """Use the same minimal properties for the synthetic location object."""


def __getattr__(name):
    """Expose one exact public mixed-case name for generated-SDK controls."""
    if name == 'aimmsMachine':
        return Machine
    raise AttributeError(name)

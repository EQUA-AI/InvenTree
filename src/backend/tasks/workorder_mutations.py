"""Shared authorization for proposed Work asset and ownership changes."""

from collections.abc import Mapping

from django.http import Http404

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.serializers import IntegerField

from .models import WorkOrder
from .scope import (
    ScopeError,
    require_machine_scope,
    require_work_order_scope,
    scoped_machines,
)
from .services.work_orders import CommandConflict, require_work_order_machine_change


def object_data(data):
    """Reject non-object request bodies before inspecting parent identifiers."""
    if not isinstance(data, Mapping):
        raise ValidationError('Expected an object.')
    return data


def parent_id(data, field, default=None):
    """Validate identifier syntax without looking up an unscoped foreign key."""
    value = data.get(field, default)
    if value is None:
        return None
    try:
        return IntegerField().run_validation(value)
    except ValidationError as exc:
        raise ValidationError({field: exc.detail}) from exc


class PacketMachineConflict(APIException):
    """A repair packet owns the asset as well as the execution lifecycle."""

    status_code = 409
    default_code = 'PACKET_OWNS_LIFECYCLE'
    default_detail = (
        'A repair packet owns this Work lifecycle; its machine cannot be changed.'
    )


def require_work_order_candidate(actor, serializer):
    """Check the resulting Work scope and any newly selected machine."""
    instance = serializer.instance
    values = serializer.validated_data
    candidate = WorkOrder(
        machine=values.get('machine', getattr(instance, 'machine', None)),
        customer=values.get('customer', getattr(instance, 'customer', None)),
    )
    if instance is not None:
        try:
            require_work_order_machine_change(instance, candidate.machine_id)
        except CommandConflict as exc:
            raise PacketMachineConflict() from exc
    try:
        require_work_order_scope(actor, candidate)
        if instance is None or candidate.machine_id != instance.machine_id:
            if candidate.machine_id is not None:
                require_machine_scope(actor, candidate.machine)
    except ScopeError as exc:
        raise Http404 from exc


class ObjectInputMixin:
    """Reject array bodies before the native input sanitizer traverses them."""

    def clean_data(self, data):
        """Retain native sanitization after validating the container shape."""
        return super().clean_data(object_data(data))


class WorkOrderMachineInputMixin(ObjectInputMixin):
    """Authorize a proposed asset before FK validation discloses its existence."""

    def get_serializer(self, *args, **kwargs):
        """Validate request shape and scope the proposed machine lookup."""
        if 'data' in kwargs:
            data = object_data(kwargs['data'])
            instance = args[0] if args else kwargs.get('instance')
            machine_id = parent_id(data, 'machine')
            if machine_id is not None and (
                instance is None or machine_id != instance.machine_id
            ):
                get_object_or_404(scoped_machines(self.request.user), pk=machine_id)
        return super().get_serializer(*args, **kwargs)

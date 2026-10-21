"""Calibration traceability uses the native scoped closeout command boundary."""

from django.shortcuts import get_object_or_404

from rest_framework.response import Response

from tasks.calibration_serializers import CalibrationCommandSerializer
from tasks.closeout_api import CloseoutCommandView
from tasks.closeout_models import CloseoutReading
from tasks.services.calibration import command


class CalibrationTraceabilityCommand(CloseoutCommandView):
    """Explicit record/correct intents; overview supplies the read projection."""

    def post(self, request, pk, reading):
        """Resolve both parents before accepting fields or exposing conflicts."""
        work = self.get_work_order(request, pk)
        get_object_or_404(CloseoutReading, pk=reading, work_order=work)
        serializer = CalibrationCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.run(
            request, work, command, reading_id=reading, **serializer.validated_data
        )
        return result if isinstance(result, Response) else Response(result)

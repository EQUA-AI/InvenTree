"""PDF downloads under the same current Work scope as the source reports."""

from django.http import Http404, HttpResponse

from rest_framework import serializers
from rest_framework.response import Response

from tasks.report_api import ScopedRecordView
from tasks.report_models import AcceptedCloseoutReport
from tasks.services.closeout_pdf import render_closeout_pdf
from tasks.services.service_reports import latest_report, metadata


class DraftAccountSerializer(serializers.Serializer):
    """Drafts may be incomplete; PDF rendering never validates completion."""

    cause = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    action = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    result = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    verification_summary = serializers.CharField(
        required=False, allow_blank=True, max_length=20000
    )
    follow_up = serializers.CharField(
        required=False, allow_blank=True, max_length=20000
    )
    follow_up_required = serializers.BooleanField(required=False)
    downtime_minutes = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )


class PDFRequestSerializer(serializers.Serializer):
    """Only a draft or an exact retained service-report identity is accepted."""

    source = serializers.ChoiceField(choices=['draft', 'submitted'])
    expected_version = serializers.IntegerField(min_value=1)
    report_id = serializers.IntegerField(required=False, min_value=1)
    account = DraftAccountSerializer(required=False)


def pdf_response(work, **document):
    """Keep private derived bytes uncached and use a numeric filename."""
    response = HttpResponse(
        render_closeout_pdf(work_order=work, **document), content_type='application/pdf'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="work-{work.pk}-service-report.pdf"'
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class WorkCloseoutPDF(ScopedRecordView):
    """No draft, submission, approval or lifecycle write happens here."""

    def post(self, request, pk):
        """Reject changed versions and render retained submissions server-side."""
        work = self.get_work_order(request, pk)
        values = PDFRequestSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        data = values.validated_data
        if data['expected_version'] != work.lifecycle_version:
            return Response(
                {'detail': 'Work changed. Reload before exporting.'}, status=409
            )
        if data['source'] == 'submitted':
            submission = latest_report(work)
            if not submission or submission.pk != data.get('report_id'):
                raise Http404
            retained = metadata(submission)
            return pdf_response(
                work,
                account=retained['document']['account'],
                status='SUBMITTED SNAPSHOT · NOT A COMPLETION RECORD',
                identity=retained['document'],
                content_hash=retained['content_hash'],
            )
        return pdf_response(
            work, account=data.get('account', {}), status='DRAFT · NOT SUBMITTED'
        )


class WorkAcceptedReportPDF(ScopedRecordView):
    """Use the selected immutable revision, never today's mutable account."""

    def get(self, request, pk, report_id):
        """Apply present-day scope to historical accepted PDFs."""
        work = self.get_work_order(request, pk)
        report = AcceptedCloseoutReport.objects.filter(
            pk=report_id, closeout__work_order=work
        ).first()
        if report is None:
            raise Http404
        return pdf_response(
            work,
            account=report.document['closeout'],
            status=f'FROZEN COMPLETION · REVISION {report.revision}',
            identity=report.document['work'],
            text=report.text,
            content_hash=report.content_hash,
        )

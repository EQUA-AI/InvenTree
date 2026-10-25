"""Local, scoped PDF projections; rendering never records approval or completion."""

from django.template.loader import render_to_string

from report.fetcher import InvenTreeURLFetcher


class CloseoutPDFFetcher(InvenTreeURLFetcher):
    """Self-contained closeout documents cannot fetch network or local files."""

    def fetch(self, url, headers=None):
        """Reject every external resource, including URLs embedded in text."""
        raise ValueError('Closeout PDFs do not load external resources')


def render_closeout_pdf(
    *, work_order, account, status, text='', content_hash=None, identity=None
):
    """Render escaped structured fields with explicit document provenance."""
    from weasyprint import HTML

    labels = {
        'cause': 'Cause / finding',
        'action': 'Action taken',
        'result': 'Observed result',
        'verification_summary': 'Verification recorded',
        'downtime_minutes': 'Downtime (minutes)',
        'follow_up_required': 'Follow-up required',
        'follow_up': 'Follow-up',
    }
    rows = []
    for name, label in labels.items():
        value = account.get(name)
        if isinstance(value, bool):
            value = 'Yes' if value else 'No'
        elif value is None or value == '':
            value = 'Not recorded'
        rows.append({'label': label, 'value': value})
    html = render_to_string(
        'report/closeout_pdf.html',
        {
            'work': identity
            if identity is not None
            else {
                'reference': work_order.reference,
                'title': work_order.title,
                'machine_name': work_order.machine.name
                if work_order.machine_id
                else None,
            },
            'rows': rows,
            'status': status,
            'text': text,
            'content_hash': content_hash,
        },
    )
    return HTML(string=html, url_fetcher=CloseoutPDFFetcher()).write_pdf()

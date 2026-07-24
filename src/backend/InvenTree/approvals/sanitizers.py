"""Payload sanitization utilities for the approvals system.

Implements:
- D-5: HTML sanitization of email body content to prevent XSS
- S-7: Execution error redaction to avoid leaking secrets
"""

import re

# Patterns that might indicate sensitive data
_SENSITIVE_PATTERNS = [
    re.compile(
        r'(?i)(password|passwd|secret|token|api_key|apikey|auth|credential)'
        r'\s*[:=]\s*\S+'
    ),
    re.compile(r'(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'(?i)(connection\s*string|conn\s*str)\s*[:=]\s*\S+'),
]

_REDACTION = '[REDACTED]'


def sanitize_email_html(html_content: str) -> str:
    """Sanitize HTML content from email payloads to prevent XSS.

    Strips script/style tags, event handler attributes, and javascript: URIs.
    """
    if not html_content or not isinstance(html_content, str):
        return html_content or ''

    # Strip script/style tags and their contents
    html_content = re.sub(
        r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE
    )
    html_content = re.sub(
        r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE
    )

    # Strip event handler attributes (on*)
    html_content = re.sub(
        r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', html_content, flags=re.IGNORECASE
    )
    html_content = re.sub(r'\s+on\w+\s*=\s*\S+', '', html_content, flags=re.IGNORECASE)

    # Strip javascript: URIs
    html_content = re.sub(
        r'(?i)(href|src|action)\s*=\s*["\']?\s*javascript:', r'\1="', html_content
    )

    return html_content


def sanitize_payload(payload: dict, action_type: str) -> dict:
    """Sanitize approval payload content based on action type.

    For email payloads, sanitizes HTML body content.
    Returns a new dict (does not mutate the original).
    """
    if not isinstance(payload, dict):
        return payload

    payload = payload.copy()

    if action_type == 'email':
        if 'body' in payload and isinstance(payload['body'], str):
            payload['body'] = sanitize_email_html(payload['body'])
        if 'body_html' in payload and isinstance(payload['body_html'], str):
            payload['body_html'] = sanitize_email_html(payload['body_html'])

    return payload


def redact_error(error_data) -> dict:
    """Redact sensitive information from execution error data.

    Strips passwords, tokens, connection strings, and stack traces.
    Returns a sanitized dict.
    """
    if error_data is None:
        return {}

    if isinstance(error_data, str):
        result = error_data
        for pattern in _SENSITIVE_PATTERNS:
            result = pattern.sub(_REDACTION, result)
        # Strip Python stack traces
        result = re.sub(
            r'Traceback \(most recent call last\):.*?(?=\n\S|\Z)',
            'Traceback [REDACTED]',
            result,
            flags=re.DOTALL,
        )
        return {'error': result}

    if isinstance(error_data, dict):
        redacted = {}
        for key, value in error_data.items():
            if isinstance(value, str):
                text = value
                for pattern in _SENSITIVE_PATTERNS:
                    text = pattern.sub(_REDACTION, text)
                redacted[key] = text
            elif isinstance(value, dict):
                redacted[key] = redact_error(value)
            else:
                redacted[key] = value
        return redacted

    return {'error': str(error_data)}

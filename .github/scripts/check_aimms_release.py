"""Read-only release gate using an operator's exported Netscape session cookies.

Run against a candidate revision and again against the normal app host after
traffic moves. Never print cookies, response bodies, or private record data.
"""

import argparse
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

METRICS = (
    'open',
    'completed',
    'overdue',
    'preventive',
    'assignment',
    'age',
    'alerts',
    'holds',
    'verification',
    'pm-on-time',
    'elapsed',
    'mix',
    'repeat',
    'downtime',
    'data-health',
)


class SmokeError(Exception):
    """A sanitized failure suitable for deployment logs."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not a successful authenticated API response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Keep credentials and identity checks on the requested origin."""
        raise SmokeError('Unexpected HTTP redirect')


def require(condition, message):
    """Fail a deployment gate without including response data."""
    if not condition:
        raise SmokeError(message)


def check_release(read, commit, frontend_path='/static/web/build-info.json'):
    """Check actual API contracts, not just healthy processes or HTTP 200s."""
    require(read('/health/live').get('status') == 'alive', 'Liveness contract failed')
    require(read('/health/ai-ready').get('status') == 'ready', 'AI readiness failed')
    capabilities = read('/api/aichat/ui/capabilities/')
    require(capabilities.get('version') == 1, 'Unsupported UI capability contract')
    require(capabilities.get('backend_commit') == commit, 'Backend commit mismatch')
    require(
        capabilities.get('maintenance_metrics') == 1, 'Maintenance contract unavailable'
    )
    frontend = read(frontend_path)
    require(frontend.get('commit') == commit, 'Frontend commit mismatch')
    require(
        frontend.get('dirty') is False, 'Frontend was built from an unverified tree'
    )
    require(frontend.get('ui_contract') == 1, 'Frontend contract mismatch')
    require(
        isinstance(read('/api/ai/threads').get('threads'), list),
        'Thread-list contract failed',
    )
    require(
        isinstance(read('/api/ai/voice/capability').get('enabled'), bool),
        'Voice capability contract failed',
    )
    require(
        isinstance(read('/api/aichat/proposals/').get('results'), list),
        'Proposal-list contract failed',
    )
    radar = capabilities.get('risk_radar')
    require(isinstance(radar, bool), 'Missing Risk Radar capability')
    if radar:
        scopes = read('/api/repair/risk-scopes/')
        require(isinstance(scopes.get('scopes'), list), 'Risk-scope contract failed')
        require(
            bool(scopes.get('authorization_fingerprint')),
            'Missing risk authorization fingerprint',
        )
    else:
        read('/api/repair/risk-scopes/', expected=404)
    for metric in METRICS:
        data = read(
            '/api/aichat/ui/maintenance-metrics/?'
            + urllib.parse.urlencode({'metric': metric})
        )
        require(
            data.get('id') == metric and data.get('version') == 1,
            'Maintenance result contract failed',
        )
        require(
            data.get('state') == 'ready',
            'Maintenance scope unavailable to smoke-test operator',
        )
        require(isinstance(data.get('records'), list), 'Maintenance records missing')
    return {'status': 'passed', 'commit': commit, 'maintenance_metrics': len(METRICS)}


def main():
    """Load cookies from a private file, perform GETs, and emit only a verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--cookie-file', required=True)
    parser.add_argument('--expect-commit', required=True)
    parser.add_argument('--frontend-path', default='/static/web/build-info.json')
    parser.add_argument('--timeout', type=float, default=15)
    args = parser.parse_args()
    base = urllib.parse.urlsplit(args.base_url)
    require(
        base.scheme == 'https'
        or (base.scheme == 'http' and base.hostname in ('localhost', '127.0.0.1')),
        'Use HTTPS except for local testing',
    )
    require(
        not base.username
        and not base.password
        and not base.query
        and not base.fragment
        and base.path in ('', '/'),
        'Base URL must be an origin',
    )
    require(
        bool(re.fullmatch(r'[a-f0-9]{40}', args.expect_commit)),
        'Expected commit must be a full Git SHA',
    )
    require(
        args.frontend_path.startswith('/') and not args.frontend_path.startswith('//'),
        'Frontend path must stay on the selected origin',
    )
    cookies = http.cookiejar.MozillaCookieJar(args.cookie_file)
    cookies.load(ignore_discard=True)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies), NoRedirect()
    )

    def read(path, expected=200):
        """Reject HTML/login pages and unexpected statuses without logging bodies."""
        request = urllib.request.Request(
            args.base_url.rstrip('/') + path,
            headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'},
        )
        try:
            response = opener.open(request, timeout=args.timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except urllib.error.URLError:
            raise SmokeError(f'Network failure: {path.split("?")[0]}') from None
        with response:
            require(
                response.status == expected,
                f'HTTP {response.status}, expected {expected}: {path.split("?")[0]}',
            )
            if expected != 200:
                return {}
            require(
                'application/json' in response.headers.get('Content-Type', ''),
                'Expected JSON response',
            )
            try:
                data = json.load(response)
            except (ValueError, UnicodeError):
                raise SmokeError('Invalid JSON response') from None
            require(isinstance(data, dict), 'Expected JSON object')
            return data

    print(json.dumps(check_release(read, args.expect_commit, args.frontend_path)))


if __name__ == '__main__':
    try:
        main()
    except (SmokeError, OSError) as exc:
        # Cookie files and network errors must not leak credentials in CI output.
        print(
            json.dumps({
                'status': 'failed',
                'reason': str(exc)
                if isinstance(exc, SmokeError)
                else 'Cookie file or network unavailable',
            })
        )
        raise SystemExit(1) from None

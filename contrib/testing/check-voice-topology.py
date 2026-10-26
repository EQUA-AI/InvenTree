"""Read-only release guard for process-local live voice transport ownership."""

import json
import sys


def validate(document: dict) -> list[str]:
    """Accept one process/replica, or supported cookie-affine HTTP routing."""
    properties = document['properties']
    config, template = properties['configuration'], properties['template']
    problems = []
    containers = template['containers']
    web = containers[0]
    env = {r['name']: r.get('value') for r in web.get('env', [])}
    if env.get('INVENTREE_GUNICORN_WORKERS') != '1':
        problems.append('Voice requires exactly one serving process per replica.')
    maximum = template.get('scale', {}).get('maxReplicas', 10)
    ingress = config.get('ingress') or {}
    affine = (
        config.get('activeRevisionsMode') == 'Single'
        and (ingress.get('stickySessions') or {}).get('affinity') == 'sticky'
        and str(ingress.get('transport', '')).casefold() in ('auto', 'http', 'http2')
    )
    if maximum != 1 and not affine:
        problems.append(
            'Use maxReplicas=1, or Single revision mode with HTTP sticky sessions.'
        )
    traffic = [r for r in ingress.get('traffic', []) if r.get('weight', 0) > 0]
    if config.get('activeRevisionsMode') == 'Multiple' and (
        len(traffic) != 1 or traffic[0].get('weight') != 100
    ):
        problems.append(
            'Multiple revision mode must route all voice traffic to one revision.'
        )
    return problems


if __name__ == '__main__':
    with open(sys.argv[1], encoding='utf-8') as source:
        errors = validate(json.load(source))
    print(json.dumps({'voice_topology_valid': not errors, 'errors': errors}))
    raise SystemExit(bool(errors))

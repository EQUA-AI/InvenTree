#!/usr/bin/env python3
"""Release-image harness for the EQUA demo-metrics production-image candidate.

Harness-only (never used by any deployment). Stdlib-only, offline. Subcommands:

* ``freeze``      — build the frozen ALLOWLISTED build context under the scratch
                    directory and write the input-sha manifest. The context is
                    git-tracked build inputs plus explicitly named new demo
                    source/harness files; credentials, local state, IDEA.md,
                    LocalDocs, node_modules/dev/venv are denied (fail closed).
* ``parity``      — source/image hash parity: hash the image-contained backend
                    source and compare it with the frozen input-sha manifest.
                    The source is either ``--image`` (parity launches a fresh
                    ``docker run``) or ``--container`` (parity inspects that
                    EXISTING container in place via ``docker exec``); exactly
                    one source is required and a missing/ambiguous source
                    fails closed.
* ``scan-log``    — scan a server-side SQL statement log for write-shaped
                    statements or write-refusals (fail closed unless
                    ``--expect-write``). The capture must contain real server
                    statement evidence; an empty capture or a daemon error
                    message never counts as "zero write attempts".
* ``compare-fingerprints`` — compare before/after row-count fingerprints across
                    the read-only probe window (fail closed).
* ``refusal-pattern`` — print the server-log refusal regex that must match a
                    write-defense SQLSTATE (the negative probe's separately
                    validated server refusal).

The candidate built from this context is an UNCOMMITTED LOCAL CANDIDATE: the
``commit_hash`` build argument is deliberately empty and HEAD is never claimed
as the commit of the uncommitted demo implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import demo_metrics_release_guard as guard


def _git(repo_root: str, *args: str) -> str:
    out = subprocess.run(['git', *args], cwd=repo_root, capture_output=True, check=True)
    return out.stdout.decode('utf-8').strip()


def cmd_freeze(args) -> int:
    """Freeze the allowlisted build context and write the input-sha manifest."""
    repo_root = os.path.abspath(args.repo)
    dest = os.path.abspath(args.dest)
    context = os.path.join(dest, 'context')
    if os.path.exists(os.path.join(dest, 'input-sha-manifest.json')):
        raise guard.ReleaseGuardError(
            f'refusing to overwrite an existing frozen context: {dest!r}'
        )
    include = guard.plan_context_files(repo_root)
    dirty = []
    for line in _git(repo_root, 'status', '--porcelain').splitlines():
        xy, rest = line[:2], line[2:].strip()
        if rest and set(xy) & set('MADRCU'):
            dirty.append(rest.split(' -> ')[-1])
    head = _git(repo_root, 'rev-parse', 'HEAD')

    for rel in include:
        src = os.path.join(repo_root, rel)
        dst = os.path.join(context, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, 'rb') as fin, open(dst, 'wb') as fout:
            fout.write(fin.read())

    manifest = guard.build_manifest(
        include, repo_root=repo_root, head_commit=head, git_dirty_tracked=dirty
    )
    manifest_path = os.path.join(dest, 'input-sha-manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
        handle.write('\n')

    print('FROZEN BUILD CONTEXT OK')
    print('candidate_status:', manifest['candidate_status'])
    print('commit_hash_build_arg:', repr(manifest['commit_hash_build_arg']))
    print('context:', context)
    print('manifest:', manifest_path)
    print('files:', manifest['file_count'])
    print('files_digest:', manifest['files_digest'])
    tracked = sum(1 for v in include.values() if v == 'tracked')
    print(f'  tracked build inputs: {tracked}')
    print(f'  named new demo source/harness files: {len(include) - tracked}')
    print('dirty tracked files recorded:', len(manifest['git_dirty_tracked_files']))
    return 0


def parity_argv(*, image=None, container=None) -> list:
    """Select the docker argv prefix for the in-container source hashing.

    Exactly one source must be named. ``container`` inspects an EXISTING
    container in place via ``docker exec`` (no new container is launched);
    ``image`` launches a fresh throwaway ``docker run``. A missing or
    ambiguous source fails closed — there is no default target.
    """
    if (not image) == (not container):
        raise guard.ReleaseGuardError(
            'parity requires exactly one source: --image (fresh container) '
            'or --container (hash inspection of an existing container)'
        )
    if container:
        return ['docker', 'exec', '-i', container, 'sh', '-c']
    return ['docker', 'run', '--rm', '-i', '--entrypoint', 'sh', image, '-c']


def cmd_parity(args) -> int:
    """Compare in-image source hashes against the frozen input manifest."""
    argv = parity_argv(image=args.image, container=args.container)
    manifest_path = os.path.abspath(args.manifest)
    if not os.path.isfile(manifest_path):
        raise guard.ReleaseGuardError(f'manifest not found: {manifest_path!r}')
    with open(manifest_path, encoding='utf-8') as handle:
        manifest = json.load(handle)

    expected = {
        rel: info['sha256']
        for rel, info in manifest['files'].items()
        if guard.image_path_for(rel) is not None
    }
    mapping = {rel: guard.image_path_for(rel) for rel in expected}
    image_paths = sorted({p for p in mapping.values() if p})
    # Pass the path list over stdin (not argv): ~2k paths exceed ARG_MAX.
    hash_py = (
        'import hashlib,sys\n'
        'for line in sys.stdin:\n'
        '    p=line.rstrip("\\n")\n'
        '    if not p: continue\n'
        '    try:\n'
        '        h=hashlib.sha256(open(p,"rb").read()).hexdigest()\n'
        '    except Exception as exc:\n'
        '        print("MISSING", p, exc, file=sys.stderr)\n'
        '        raise SystemExit(2)\n'
        '    print(h, p)\n'
    )
    result = subprocess.run(
        [*argv, '/usr/local/bin/python3 -c ' + sh_quote(hash_py)],
        input=''.join(p + '\n' for p in image_paths),
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout

    actual = {}
    for line in lines.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            actual[parts[-1]] = parts[0]

    missing, mismatched = [], []
    for rel, want in sorted(expected.items()):
        got = actual.get(mapping[rel])
        if got is None:
            missing.append(mapping[rel])
        elif got != want:
            mismatched.append((rel, mapping[rel], want, got))

    report = {
        'image': args.image,
        'container': args.container,
        'manifest': manifest_path,
        'files_digest': manifest['files_digest'],
        'compared': len(expected),
        'missing_in_image': missing,
        'hash_mismatch': [
            {'path': rel, 'image_path': ip, 'manifest': w, 'image': g}
            for rel, ip, w, g in mismatched
        ],
    }
    out_path = args.out
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
            handle.write('\n')

    if missing or mismatched:
        print('SOURCE/IMAGE PARITY FAILED', file=sys.stderr)
        for _rel, ip, w, g in mismatched:
            print(f'  MISMATCH {ip}: manifest={w} image={g}', file=sys.stderr)
        for ip in missing:
            print(f'  MISSING {ip}', file=sys.stderr)
        return 1
    print('SOURCE/IMAGE PARITY OK')
    print('compared files:', len(expected))
    print('files_digest:', manifest['files_digest'])
    return 0


def sh_quote(value: str) -> str:
    """Single-quote ``value`` for a POSIX ``sh -c`` command string."""
    return "'" + value.replace("'", "'\\''") + "'"


def cmd_scan_log(args) -> int:
    """Scan a server statement log for writes, requiring real SQL evidence."""
    with open(args.log, encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    try:
        statements = guard.require_statement_evidence(text)
    except guard.ReleaseGuardError as exc:
        print(f'SCAN FAILED ({args.label}): {exc}', file=sys.stderr)
        return 1
    shaped, refused = guard.scan_statement_log(text)
    hits = [*shaped, *refused]
    print(
        f'{args.label}: server-statements={statements} '
        f'write-shaped={len(shaped)} write-refused={len(refused)}'
    )
    for line in hits[:20]:
        print('  ', line[:200])
    if args.expect_write:
        if not hits:
            print(
                f'SCAN FAILED ({args.label}): expected a write attempt, none found',
                file=sys.stderr,
            )
            return 1
        print(f'SCAN OK ({args.label}): write attempt recorded as expected')
        return 0
    if hits:
        print(
            f'SCAN FAILED ({args.label}): write-shaped/refused SQL reached the server',
            file=sys.stderr,
        )
        return 1
    print(f'SCAN OK ({args.label}): zero write attempts reached the server')
    return 0


def cmd_compare_fingerprints(args) -> int:
    """Compare before/after row-count fingerprints (counts only, fail closed)."""
    with open(args.before, encoding='utf-8') as handle:
        before = json.load(handle)
    with open(args.after, encoding='utf-8') as handle:
        after = json.load(handle)
    summary = guard.compare_fingerprints(before, after, expect_user=args.expect_user)
    print('FINGERPRINT UNCHANGED: zero row-count changes across the window')
    print(
        f'  tables={summary["tables"]} rows={summary["total_rows"]} '
        f'user={summary["user"]} (row COUNTS only, NOT a content checksum; '
        'content-preserving writes need server statement-log evidence)'
    )
    return 0


def cmd_refusal_pattern(args) -> int:
    """Print the server-log refusal regex for a write-defense SQLSTATE."""
    print(guard.write_refusal_pattern_for(args.sqlstate))
    return 0


def main(argv=None) -> int:
    """Run the release-image harness CLI; guard refusals exit nonzero."""
    parser = argparse.ArgumentParser(
        prog='demo_metrics_release_image',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('freeze', help='freeze the allowlisted build context')
    p.add_argument('--repo', required=True)
    p.add_argument('--dest', required=True)
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser('parity', help='source/image hash parity check')
    p.add_argument(
        '--image', help='candidate image (parity launches a fresh docker run)'
    )
    p.add_argument(
        '--container',
        help='existing container (parity inspects it in place via docker exec)',
    )
    p.add_argument('--manifest', required=True)
    p.add_argument('--out', required=True)
    p.set_defaults(func=cmd_parity)

    p = sub.add_parser('scan-log', help='server statement log write scan')
    p.add_argument('--log', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--expect-write', action='store_true')
    p.set_defaults(func=cmd_scan_log)

    p = sub.add_parser('compare-fingerprints', help='before/after row-count comparison')
    p.add_argument('--before', required=True)
    p.add_argument('--after', required=True)
    p.add_argument('--expect-user', required=True)
    p.set_defaults(func=cmd_compare_fingerprints)

    p = sub.add_parser(
        'refusal-pattern', help='server-log refusal regex for a write-defense SQLSTATE'
    )
    p.add_argument('--sqlstate', required=True)
    p.set_defaults(func=cmd_refusal_pattern)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except guard.ReleaseGuardError as exc:
        print(f'RELEASE GUARD REFUSED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

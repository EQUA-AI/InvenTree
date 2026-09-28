"""Focused offline tests for the demo-metrics release-image harness.

Harness-only (never used by any deployment). Stdlib-only and offline; any
temporary files go through ``tempfile`` (which honors ``TMPDIR``), never the
system temp dir. These are fabricated unit-test fixtures — NOT acceptance
evidence — and they cover the pure guard/allowlist logic of
``demo_metrics_release_guard`` / ``demo_metrics_release_image``:

* the release-probe database name/host guards (exact-name, fail closed),
* the frozen build-context allowlist (tracked build inputs + explicitly named
  new demo source/harness files) and its credential/local-state denylist,
* the in-image path mapping used for source/image hash parity,
* the write-shaped server statement-log scan,
* the before/after row-count fingerprint comparison,
* the canonical input-manifest digest.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import demo_metrics_release_guard as guard
import demo_metrics_release_image as release_image


class TestProbeDbGuards(unittest.TestCase):
    """Exact-name guards for the release probe database and host."""

    def test_exact_name_accepted(self):
        """The exact disposable probe DB name is accepted."""
        self.assertEqual(
            guard.check_probe_db_name('inventree_dm_release_probe_v1'),
            'inventree_dm_release_probe_v1',
        )

    def test_default_and_other_databases_refused(self):
        """Any other database name is refused, fail closed."""
        for bad in (
            'inventree',
            'inventree_dm_bootstrap_v1',
            'postgres',
            'template1',
            'inventree_dm_release_probe_v1 ',
            'Inventree_dm_release_probe_v1',
            'inventree_dm_release_probe_v2',
            '',
        ):
            with self.assertRaises(guard.ReleaseGuardError, msg=bad):
                guard.check_probe_db_name(bad)

    def test_probe_host_accepted(self):
        """The dedicated probe DB host is accepted."""
        self.assertEqual(
            guard.check_probe_db_host('inventree_dm_release_probe_db'),
            'inventree_dm_release_probe_db',
        )

    def test_cloud_and_unknown_hosts_refused(self):
        """Cloud and unknown hosts are refused."""
        for bad in (
            'postgres.database.azure.com',
            'epconchat-pg-dev.postgres.database.azure.com',
            'something.database.windows.net',
            'db.example.com',
            '',
        ):
            with self.assertRaises(guard.ReleaseGuardError, msg=bad):
                guard.check_probe_db_host(bad)


class TestDenyList(unittest.TestCase):
    """Credential/local-state denylist decisions for frozen-context paths."""

    def test_forbidden_paths_are_flagged(self):
        """Credentials, local state and caches are denied."""
        for path in (
            'IDEA.md',
            '.env',
            '.env.local',
            '.env.production',
            'contrib/container/docker.dev.env',
            'secrets/creds.json',
            'LocalDocs/UiUpgrades/plan.md',
            'LocalTesting/state.json',
            'node_modules/left-pad/index.js',
            'src/frontend/node_modules/x.js',
            'dev/venv/lib.py',
            '.venv/pyvenv.cfg',
            'src/backend/InvenTree/__pycache__/x.pyc',
            'src/backend/InvenTree/key.pem',
            'id_rsa',
            'app/config.secret.yaml',
            'infra/credentials.json',
            'deploy/prod-secrets.yaml',
        ):
            self.assertIsNotNone(guard.deny_reason(path), msg=path)

    def test_legitimate_paths_are_not_flagged(self):
        """Legitimate build inputs and source files are not denied."""
        for path in (
            'tasks.py',
            'pyproject.toml',
            'contrib/container/Dockerfile',
            'contrib/container/demo_metrics_release_guard.py',
            'src/backend/InvenTree/assets/demo_metrics/planner.py',
            'src/frontend/src/pages/assets/locations/demoMetrics.ts',
            'src/backend/InvenTree/InvenTree/settings.py',
            'src/backend/InvenTree/aichat/services/email/credentials.py',
            'contrib/container/gcp-entra-token.sh',
            'src/backend/InvenTree/ai/.env.template',
        ):
            self.assertIsNone(guard.deny_reason(path), msg=path)


class TestPlanContext(unittest.TestCase):
    """Frozen-context allowlist planning over fabricated repos."""

    def _fabricated_repo(self, root: Path):
        for rel in (
            *guard.NAMED_TRACKED_BUILD_INPUTS,
            'src/backend/InvenTree/app.py',
            'src/frontend/package.json',
            'src/backend/InvenTree/assets/demo_metrics/planner.py',
        ):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('x')
        for rel in guard.NAMED_NEW_FILES:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('x')

    def test_allowlist_is_tracked_plus_named_new_only(self):
        """The allowlist is tracked build inputs plus named new files only."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fabricated_repo(root)
            # Unrelated junk exists in the tree but must never be swept in.
            for rel in ('IDEA.md', 'LocalDocs/plan.md', 'notes.txt'):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('x')
            include = guard.plan_context_files(
                root,
                tracked_files=[
                    *guard.NAMED_TRACKED_BUILD_INPUTS,
                    'src/backend/InvenTree/app.py',
                    'src/frontend/package.json',
                    'docs/readme.md',  # tracked but not a build input
                    '.github/workflows/ci.yml',  # tracked but not a build input
                ],
            )
            self.assertIn('src/backend/InvenTree/app.py', include)
            self.assertIn('src/frontend/package.json', include)
            self.assertIn('tasks.py', include)
            self.assertIn('contrib/container/Dockerfile', include)
            self.assertIn(
                'src/backend/InvenTree/assets/demo_metrics/planner.py', include
            )
            self.assertNotIn('docs/readme.md', include)
            self.assertNotIn('.github/workflows/ci.yml', include)
            self.assertNotIn('IDEA.md', include)
            self.assertNotIn('LocalDocs/plan.md', include)
            self.assertNotIn('notes.txt', include)

    def test_missing_named_file_fails_closed(self):
        """A missing named file aborts the freeze, fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fabricated_repo(root)
            (root / guard.NAMED_NEW_FILES[0]).unlink()
            with self.assertRaises(guard.ReleaseGuardError):
                guard.plan_context_files(root, tracked_files=[])

    def test_forbidden_tracked_file_fails_closed(self):
        """A denylisted tracked file aborts the freeze."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fabricated_repo(root)
            (root / 'src/evil.pem').write_text('x')
            with self.assertRaises(guard.ReleaseGuardError):
                guard.plan_context_files(
                    root,
                    tracked_files=[*guard.NAMED_TRACKED_BUILD_INPUTS, 'src/evil.pem'],
                )


class TestRealRepoAllowlist(unittest.TestCase):
    """Smoke the allowlist against the real tree (read-only, offline)."""

    def test_real_tree_plan(self):
        """The real repository plan excludes all local state."""
        repo = Path(__file__).resolve().parent.parent.parent
        include = guard.plan_context_files(repo)
        self.assertNotIn('IDEA.md', include)
        self.assertNotIn('contrib/container/docker.dev.env', include)
        self.assertFalse(any(p.startswith('LocalDocs/') for p in include))
        self.assertFalse(any('node_modules' in p for p in include))
        self.assertFalse(any(p.endswith('.env') for p in include))
        self.assertIn('contrib/container/Dockerfile', include)
        self.assertIn('contrib/container/requirements.txt', include)
        self.assertIn('contrib/container/gunicorn.conf.py', include)
        self.assertIn('contrib/container/init.sh', include)
        self.assertIn('contrib/container/gcp-entra-token.sh', include)
        self.assertIn('tasks.py', include)
        self.assertIn(
            'src/backend/InvenTree/assets/demo_metrics/demo_test_support.py', include
        )
        self.assertIn(
            'src/backend/InvenTree/assets/management/commands/plan_demo_metrics.py',
            include,
        )
        self.assertIn('src/frontend/src/pages/assets/locations/demoMetrics.ts', include)
        # Local e2e run artifacts are local state, never build inputs.
        self.assertFalse(
            any('demo-metrics-e2e-results' in p for p in include),
            'e2e result artifacts must not enter the build context',
        )

    def test_named_demo_files_match_tested_manifest(self):
        """Every named new demo source file exists in the tree right now."""
        repo = Path(__file__).resolve().parent.parent.parent
        missing = [rel for rel in guard.NAMED_NEW_FILES if not (repo / rel).is_file()]
        self.assertEqual(missing, [])


class TestImagePathMapping(unittest.TestCase):
    """In-image path mapping used for source/image hash parity."""

    def test_known_mappings(self):
        """Known context paths map to their in-image paths."""
        cases = {
            'src/backend/InvenTree/assets/demo_metrics/planner.py': '/home/inventree/src/backend/InvenTree/assets/demo_metrics/planner.py',
            'src/backend/tasks/scope.py': '/home/inventree/src/backend/tasks/scope.py',
            'src/backend/requirements.txt': '/home/inventree/src/backend/requirements.txt',
            'tasks.py': '/home/inventree/tasks.py',
            'contrib/container/requirements.txt': '/home/inventree/base_requirements.txt',
            'contrib/container/gunicorn.conf.py': '/home/inventree/gunicorn.conf.py',
            'contrib/container/init.sh': '/home/inventree/init.sh',
            'contrib/container/gcp-entra-token.sh': '/usr/local/bin/gcp-entra-token',
        }
        for rel, image in cases.items():
            self.assertEqual(guard.image_path_for(rel), image, msg=rel)

    def test_unmapped_paths(self):
        """Paths not copied into the production image map to None."""
        self.assertIsNone(
            guard.image_path_for('contrib/container/demo_metrics_release_guard.py')
        )
        self.assertIsNone(guard.image_path_for('pyproject.toml'))
        self.assertIsNone(
            guard.image_path_for(
                'src/frontend/src/pages/assets/locations/demoMetrics.ts'
            )
        )
        # src/backend files outside the production-stage COPY set are NOT in
        # the image (only InvenTree/, tasks/ and requirements.txt are copied).
        self.assertIsNone(guard.image_path_for('src/backend/osv-scanner.toml'))
        self.assertIsNone(guard.image_path_for('src/backend/requirements-dev.txt'))
        self.assertIsNone(guard.image_path_for('src/backend/requirements.in'))


class TestStatementLogScan(unittest.TestCase):
    """Write-shaped/refused statement classification in server logs."""

    def test_write_shaped_and_refused_detected(self):
        """Write-shaped and refused lines are classified apart."""
        log = '\n'.join([
            'LOG:  statement: SELECT 1',
            'LOG:  statement: INSERT INTO "assets_client" ("name") VALUES (\'x\')',
            'ERROR:  cannot execute INSERT in a read-only transaction',
            'LOG:  execute <unnamed>: SELECT count(*) FROM "assets_client"',
        ])
        shaped, refused = guard.scan_statement_log(log)
        self.assertEqual(len(shaped), 1)
        self.assertEqual(len(refused), 1)

    def test_clean_select_log(self):
        """A SELECT-only log has no write-shaped/refused lines."""
        log = '\n'.join([
            'LOG:  statement: SELECT 1',
            'LOG:  statement: SHOW default_transaction_read_only',
            'LOG:  statement: SELECT count(*) FROM "assets_client"',
        ])
        shaped, refused = guard.scan_statement_log(log)
        self.assertEqual(shaped, [])
        self.assertEqual(refused, [])

    def test_update_delete_and_ddl_detected(self):
        """UPDATE/DELETE/DDL statements are write-shaped."""
        for stmt in (
            'UPDATE assets_client SET name = 1',
            'DELETE FROM assets_client',
            'CREATE TABLE x ()',
            'DROP TABLE x',
            'ALTER DATABASE x SET y',
            'TRUNCATE assets_client',
        ):
            shaped, _ = guard.scan_statement_log(f'LOG:  statement: {stmt}')
            self.assertTrue(shaped, msg=stmt)


class TestFingerprintCompare(unittest.TestCase):
    """Before/after row-count fingerprint comparison (fail closed)."""

    def _fp(self, user='dm_release_ro', tx_ro='on', tables=None):
        return {
            'user': user,
            'default_transaction_read_only': tx_ro,
            'tables': tables or {'a': 1, 'b': 2},
        }

    def test_unchanged_passes(self):
        """Identical fingerprints pass."""
        guard.compare_fingerprints(self._fp(), self._fp(), expect_user='dm_release_ro')

    def test_row_change_fails(self):
        """A row-count change fails closed."""
        with self.assertRaises(guard.ReleaseGuardError):
            guard.compare_fingerprints(
                self._fp(),
                self._fp(tables={'a': 1, 'b': 3}),
                expect_user='dm_release_ro',
            )

    def test_wrong_user_fails(self):
        """A fingerprint from the wrong user fails closed."""
        with self.assertRaises(guard.ReleaseGuardError):
            guard.compare_fingerprints(
                self._fp(user='postgres'), self._fp(), expect_user='dm_release_ro'
            )

    def test_read_only_off_fails(self):
        """A non-read-only session fingerprint fails closed."""
        with self.assertRaises(guard.ReleaseGuardError):
            guard.compare_fingerprints(
                self._fp(tx_ro='off'), self._fp(), expect_user='dm_release_ro'
            )

    def test_table_appearing_fails(self):
        """A table appearing across the window fails closed."""
        with self.assertRaises(guard.ReleaseGuardError):
            guard.compare_fingerprints(
                self._fp(),
                self._fp(tables={'a': 1, 'b': 2, 'c': 0}),
                expect_user='dm_release_ro',
            )


class TestManifestDigest(unittest.TestCase):
    """Canonical input-manifest digest computation."""

    def test_digest_is_canonical_and_sensitive(self):
        """The digest is order-insensitive but content-sensitive."""
        files = {'b.py': {'sha256': 'bb'}, 'a.py': {'sha256': 'aa'}}
        d1 = guard.manifest_digest(files)
        files2 = {'a.py': {'sha256': 'aa'}, 'b.py': {'sha256': 'bb'}}
        self.assertEqual(d1, guard.manifest_digest(files2))
        files2['a.py'] = {'sha256': 'ab'}
        self.assertNotEqual(d1, guard.manifest_digest(files2))

    def test_digest_matches_recorded_layout(self):
        """The digest matches the recorded path+sha listing layout."""
        files = {'a.py': {'sha256': 'aa'}}
        d = guard.manifest_digest(files)
        expected = guard.sha256_text('a.py aa\n')
        self.assertEqual(d, expected)


class TestCandidateLabel(unittest.TestCase):
    """Candidate labeling: no HEAD commit claim, uncommitted marker."""

    def test_no_head_claim(self):
        """The candidate never claims HEAD as its commit."""
        self.assertEqual(guard.COMMIT_HASH_BUILD_ARG, '')
        self.assertIn('UNCOMMITTED', guard.CANDIDATE_LABEL)
        self.assertIn('not an approved release', guard.CANDIDATE_LABEL)

    def test_manifest_marks_candidate(self):
        """The manifest marks the candidate uncommitted and unapproved."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tasks.py').write_text('x')
            manifest = guard.build_manifest(
                {'tasks.py': 'tracked'},
                repo_root=root,
                head_commit='deadbeef',
                git_dirty_tracked=['tasks.py'],
            )
            self.assertEqual(manifest['candidate_status'], guard.CANDIDATE_LABEL)
            self.assertFalse(manifest['approved'])
            self.assertEqual(manifest['commit_hash_build_arg'], '')
            self.assertIn('NOT the commit', manifest['head_commit_reference'])
            self.assertIn('deadbeef', manifest['head_commit_reference'])
            self.assertEqual(
                manifest['files']['tasks.py']['git_status'], 'tracked-modified'
            )
            json.dumps(manifest)  # must be JSON serializable


class TestWriteDefenseProof(unittest.TestCase):
    """Proof classification for the negative ORM write probe.

    Only a server read-only/privilege refusal (SQLSTATE 25006 or 42501) found
    anywhere in the chained exception graph counts as proof of the write
    defense. Any other exception is NOT proof and must fail closed.
    """

    class _FakeDbError(Exception):
        """Minimal stand-in for a driver/DB exception carrying SQLSTATE."""

        def __init__(self, message, sqlstate=None, pgcode=None):
            super().__init__(message)
            if sqlstate is not None:
                self.sqlstate = sqlstate
            if pgcode is not None:
                self.pgcode = pgcode

    def test_sqlstate_on_leaf_exception_is_proof(self):
        """A 25006 directly on the raised exception is the refusal proof."""
        exc = self._FakeDbError('boom', sqlstate='25006')
        self.assertEqual(guard.find_write_defense_sqlstate(exc), '25006')
        self.assertEqual(guard.require_write_defense_refusal(exc), '25006')

    def test_pgcode_is_honored_like_sqlstate(self):
        """psycopg2-style ``pgcode`` attributes carry the same proof."""
        exc = self._FakeDbError('boom', pgcode='42501')
        self.assertEqual(guard.find_write_defense_sqlstate(exc), '42501')

    def test_cause_chain_is_searched(self):
        """A wrapper exception (e.g. Django DatabaseError) proves via __cause__."""
        cause = self._FakeDbError('server refused', sqlstate='25006')
        wrapper = RuntimeError('wrapper')
        wrapper.__cause__ = cause
        self.assertEqual(guard.find_write_defense_sqlstate(wrapper), '25006')

    def test_context_chain_is_searched(self):
        """Implicit chaining (__context__) is searched too."""
        cause = self._FakeDbError('server refused', pgcode='42501')
        try:
            try:
                raise cause
            except self._FakeDbError:
                raise RuntimeError('wrapper')
        except RuntimeError as exc:
            self.assertEqual(guard.find_write_defense_sqlstate(exc), '42501')

    def test_unrelated_exception_is_not_proof(self):
        """A random exception (import error, timeout, ValueError) is NOT proof."""
        for exc in (ValueError('x'), TimeoutError('t'), RuntimeError('r')):
            self.assertIsNone(guard.find_write_defense_sqlstate(exc), msg=exc)
            with self.assertRaises(guard.ReleaseGuardError):
                guard.require_write_defense_refusal(exc)

    def test_db_error_without_refusal_sqlstate_is_not_proof(self):
        """A database error that is not read-only/privilege is NOT proof."""
        exc = self._FakeDbError('serialization failure', sqlstate='40001')
        self.assertIsNone(guard.find_write_defense_sqlstate(exc))
        with self.assertRaises(guard.ReleaseGuardError):
            guard.require_write_defense_refusal(exc)

    def test_wrapped_non_refusal_db_error_is_not_proof(self):
        """Chaining does not launder a non-refusal SQLSTATE into proof."""
        cause = self._FakeDbError('deadlock', sqlstate='40P01')
        wrapper = RuntimeError('wrapper')
        wrapper.__cause__ = cause
        self.assertIsNone(guard.find_write_defense_sqlstate(wrapper))

    def test_refusal_pattern_per_sqlstate_matches_server_text(self):
        """Each proof SQLSTATE maps to its matching server-log refusal text."""
        ro = guard.write_refusal_pattern_for('25006')
        self.assertRegex('ERROR:  cannot execute INSERT in a read-only transaction', ro)
        self.assertNotRegex('ERROR:  permission denied for table assets_client', ro)
        priv = guard.write_refusal_pattern_for('42501')
        self.assertRegex('ERROR:  permission denied for table assets_client', priv)

    def test_non_defense_sqlstate_has_no_refusal_pattern(self):
        """Non-proof SQLSTATEs are refused by the refusal-pattern lookup."""
        for code in ('40001', '08006', '', 'XXXXX'):
            with self.assertRaises(guard.ReleaseGuardError, msg=code):
                guard.write_refusal_pattern_for(code)


class TestParityCommandSelection(unittest.TestCase):
    """Regression tests for parity's docker source selection.

    The existing-container mode must inspect the RUNNING container via
    ``docker exec`` (never launching a second container), the image mode may
    launch a fresh ``docker run``, and a missing/ambiguous source fails closed.
    """

    def test_container_mode_inspects_existing_container(self):
        """``--container`` maps to ``docker exec`` against that container."""
        argv = release_image.parity_argv(container='ctr-1', image=None)
        self.assertEqual(argv[:4], ['docker', 'exec', '-i', 'ctr-1'])
        self.assertNotIn('run', argv)
        self.assertEqual(argv[-2:], ['sh', '-c'])

    def test_image_mode_launches_fresh_container(self):
        """``--image`` maps to ``docker run --rm`` against the given image."""
        argv = release_image.parity_argv(image='img:tag', container=None)
        self.assertEqual(argv[:5], ['docker', 'run', '--rm', '-i', '--entrypoint'])
        self.assertNotIn('exec', argv)
        self.assertIn('img:tag', argv)

    def test_missing_source_fails_closed(self):
        """No image and no container is an error, never a default."""
        with self.assertRaises(guard.ReleaseGuardError):
            release_image.parity_argv(image=None, container=None)
        with self.assertRaises(guard.ReleaseGuardError):
            release_image.parity_argv(image='', container='')

    def test_ambiguous_source_fails_closed(self):
        """Both sources at once is an error, never a silent preference."""
        with self.assertRaises(guard.ReleaseGuardError):
            release_image.parity_argv(image='img:tag', container='ctr-1')

    def test_parity_cli_missing_source_exits_nonzero(self):
        """The CLI fails closed when no source is given."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps({'files': {}, 'files_digest': 'x'}))
            rc = release_image.main([
                'parity',
                '--manifest',
                str(manifest),
                '--out',
                str(Path(tmp) / 'out.json'),
            ])
            self.assertEqual(rc, 1)

    def test_parity_cli_missing_manifest_fails_closed(self):
        """A missing manifest is a guard refusal, not a traceback."""
        with tempfile.TemporaryDirectory() as tmp:
            rc = release_image.main([
                'parity',
                '--container',
                'ctr-1',
                '--manifest',
                str(Path(tmp) / 'no-such-manifest.json'),
                '--out',
                str(Path(tmp) / 'out.json'),
            ])
            self.assertEqual(rc, 1)


class TestStatementEvidence(unittest.TestCase):
    """Server SQL statement captures must contain real statement evidence.

    An empty capture or a docker daemon error message must never pass as
    "zero write attempts reached the server".
    """

    def test_empty_capture_is_not_evidence(self):
        """An empty or whitespace-only capture fails closed."""
        for text in ('', '   \n\t\n'):
            with self.assertRaises(guard.ReleaseGuardError, msg=repr(text)):
                guard.require_statement_evidence(text)

    def test_capture_error_text_is_not_evidence(self):
        """A nonempty daemon/error message is a capture failure, not evidence."""
        for text in (
            'Error response from daemon: No such container: xyz\n',
            'error fetching logs for container xyz\n',
            'No such object: xyz\n',
        ):
            with self.assertRaises(guard.ReleaseGuardError, msg=text):
                guard.require_statement_evidence(text)

    def test_startup_only_lines_are_not_statement_evidence(self):
        """Server startup chatter without SQL statements proves nothing."""
        text = '\n'.join([
            'LOG:  database system is ready to accept connections',
            'LOG:  connection received: host=172.18.0.2 port=5432',
        ])
        with self.assertRaises(guard.ReleaseGuardError):
            guard.require_statement_evidence(text)

    def test_statement_lines_are_evidence(self):
        """Real LOG statement/execute lines count as server evidence."""
        text = '\n'.join([
            'LOG:  statement: SELECT 1',
            'LOG:  execute <unnamed>: SELECT count(*) FROM "assets_client"',
            'LOG:  statement: SHOW default_transaction_read_only',
        ])
        self.assertEqual(guard.require_statement_evidence(text), 3)


class TestScanLogCliEvidence(unittest.TestCase):
    """scan-log must fail closed when the capture holds no server statements."""

    def test_error_only_capture_fails_scan(self):
        """A daemon error text cannot pass as a clean zero-attempt scan."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'log.txt'
            log.write_text('Error response from daemon: No such container: x\n')
            rc = release_image.main([
                'scan-log',
                '--log',
                str(log),
                '--label',
                'read-only window',
            ])
            self.assertEqual(rc, 1)

    def test_empty_capture_fails_scan(self):
        """An empty capture cannot pass as a clean zero-attempt scan."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'log.txt'
            log.write_text('')
            rc = release_image.main([
                'scan-log',
                '--log',
                str(log),
                '--label',
                'read-only window',
            ])
            self.assertEqual(rc, 1)

    def test_real_evidence_zero_attempts_still_passes(self):
        """The zero-attempt standard is preserved given real statement evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'log.txt'
            log.write_text('LOG:  statement: SELECT count(*) FROM "assets_client"\n')
            rc = release_image.main([
                'scan-log',
                '--log',
                str(log),
                '--label',
                'read-only window',
            ])
            self.assertEqual(rc, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)

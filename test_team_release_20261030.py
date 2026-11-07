"""Small synthetic source trees: no business DB, server or policy execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import team_release_20261030 as release


PROFILE = {'ok':True, 'baseline_id':'synthetic', 'source_equivalence':'exact_bytes'}


class TeamReleaseTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parent/'.codex-build/team-release-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.source = self.base/'workspace'; self.source.mkdir()
        for name in release.REQUIRED:
            path = self.source/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# synthetic\n' if path.suffix == '.py' else b'{}\n' if path.suffix == '.json' else b'synthetic\r\n')
        self.write('server/team_runtime.py', 'from . import db\nfrom simulation.observed_inquiry import plan_inquiries\n')
        self.write('server/db.py', '# local source, not a database\n')
        self.write('server/team_release_20261030.py', '# static entrypoint fixture\n')
        self.write('simulation/viewer_server.py', 'import os\n')
        self.write('requirements-server.txt', 'ortools==9.15.6755\n')
        for directory in ('paramedic-ui', 'patient-ui', 'hospital-ui'):
            self.write(directory+'/index.html', '<script type="module" src="app.mjs"></script>')
            self.write(directory+'/app.mjs', 'export const fixture = true;\n')
        self.write('simulation/viewer/index.html', '<link rel="stylesheet" href="styles.css"><script src="app.js"></script>')
        self.write('simulation/viewer/demo.html', '<script src="demo.js"></script>')
        self.write('simulation/viewer/styles.css', 'body{}\n'); self.write('simulation/viewer/app.js', '')
        self.write('simulation/viewer/demo.js', '')
        self.profile = patch.object(release, 'source_profile', return_value=PROFILE)
        self.profile.start(); self.addCleanup(self.profile.stop)

    def write(self, name, value):
        path = self.source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value, encoding='utf-8', newline='')

    def package(self, name='artifact'):
        output = self.base/name
        result = release.package_release(output, source_root=self.source)
        self.assertTrue(result['ok'], result); return output

    def test_snapshot_is_byte_exact_and_excludes_db_keys_build_and_raw_results(self):
        for name in ('data/current.db', '.env', '.codex-build/private.py', 'outputs/private.json',
                     'simulation/route_cache/private.json', 'simulation_results/raw.json.gz',
                     'hospital-ui/private.key', 'server/static/private.pem'):
            self.write(name, 'private sentinel, preserve')
        before = {p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        output = self.package(); manifest = json.loads((output/release.MANIFEST).read_text(encoding='utf-8'))
        self.assertEqual(before, {p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.assertNotIn('data/current.db', manifest['files']); self.assertNotIn('.env', manifest['files'])
        self.assertIn('simulation/viewer/map_replay_data.json', manifest['files'])
        for name, expected in manifest['files'].items():
            self.assertEqual((output/'source'/name).read_bytes(), before[name])
            self.assertEqual(expected['sha256'], hashlib.sha256(before[name]).hexdigest())
        self.assertTrue(release.verify_release(output, source_root=self.source)['source_matches'])

    def test_copied_release_verifies_independently_of_original_location(self):
        output = self.package(); moved = self.base/'renamed-artifact'; output.rename(moved)
        self.source.rename(self.base/'original-not-at-old-path')
        result = release.verify_release(moved); self.assertTrue(result['ok'], result)

    def test_manifest_content_id_is_stable_for_identical_bytes_but_not_changes(self):
        one = self.package('one'); two = self.package('two')
        first = release.verify_release(one); self.assertEqual(first['content_sha256'], release.verify_release(two)['content_sha256'])
        self.write('hospital-ui/app.mjs', '// changed current source\n')
        self.assertFalse(release.verify_release(one, source_root=self.source)['ok'])
        three = self.package('three'); self.assertNotEqual(first['content_sha256'], release.verify_release(three)['content_sha256'])

    def test_changed_missing_or_extra_payload_is_detected(self):
        for kind in ('changed', 'missing', 'extra'):
            with self.subTest(kind=kind):
                output = self.package(kind); target = output/'source/hospital-ui/app.mjs'
                if kind == 'changed': target.write_bytes(b'// altered')
                elif kind == 'missing': target.unlink()
                else: (output/'source/unreviewed.py').write_bytes(b'# added')
                self.assertFalse(release.verify_release(output)['ok'])

    def test_explicit_runtime_artifacts_can_change_but_new_source_cannot(self):
        output = self.package(); payload = output/'source'
        for name in ('data/team-common.db', 'data/backups/old.db', '.solver_deps/package/module.py',
                     'server/__pycache__/module.cpython-312.pyc'):
            path = payload/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'generated runtime artifact')
        self.assertTrue(release.verify_release(output)['ok'])
        (payload/'server/new_source.py').write_bytes(b'# unreviewed source')
        self.assertFalse(release.verify_release(output)['ok'])
        (payload/'server/new_source.py').unlink()
        (payload/'server/__pycache__/hidden_source.py').write_bytes(b'# not bytecode')
        self.assertFalse(release.verify_release(output)['ok'])

    def test_manifest_digest_and_policy_cannot_silently_change(self):
        output = self.package(); path = output/release.MANIFEST
        manifest = json.loads(path.read_text(encoding='utf-8')); manifest['label'] = 'changed'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertIn('Manifest digest mismatch.', release.verify_release(output)['issues'])
        manifest['policy']['solver_seconds'] = 9; manifest['manifest_sha256'] = release.digest({k:v for k,v in manifest.items() if k != 'manifest_sha256'})
        path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertIn('Release revision/policy mismatch.', release.verify_release(output)['issues'])

    def test_malformed_manifest_returns_a_failed_report(self):
        output = self.package(); path = output/release.MANIFEST
        for value in ('[]', 'null', '{"revision":1,"revision":2}', '{broken'):
            path.write_text(value, encoding='utf-8')
            self.assertFalse(release.verify_release(output)['ok'])

    def test_unsafe_and_nonallowlisted_manifest_paths_are_rejected(self):
        for name in ('../outside.py', 'C:/outside.py', 'server\\app.py', 'data/patient.db'):
            with self.subTest(name=name):
                output = self.package('unsafe-'+str(len(list(self.base.iterdir())))); path = output/release.MANIFEST
                manifest = json.loads(path.read_text(encoding='utf-8')); manifest['files'][name] = {'bytes':0, 'sha256':'0'*64, 'role':'source'}
                manifest['manifest_sha256'] = release.digest({k:v for k,v in manifest.items() if k != 'manifest_sha256'})
                path.write_text(json.dumps(manifest), encoding='utf-8')
                self.assertFalse(release.verify_release(output)['ok'])

    def test_existing_outputs_and_outputs_inside_source_trees_are_not_modified(self):
        output = self.base/'existing'; output.mkdir(); sentinel = output/'preserve.txt'; sentinel.write_text('preserve')
        with self.assertRaises(ValueError): release.package_release(output, source_root=self.source)
        self.assertEqual(sentinel.read_text(), 'preserve')
        with self.assertRaises(ValueError): release.package_release(self.source/'hospital-ui/release', source_root=self.source)

    def test_python_and_web_module_closure_gaps_block_packaging(self):
        self.write('server/team_runtime.py', 'from . import absent\n')
        with self.assertRaisesRegex(ValueError, 'absent'): self.package()
        self.assertFalse((self.base/'artifact').exists())
        self.write('server/team_runtime.py', 'import ortools\n'); self.write('requirements-server.txt', '# no exact pins\n')
        with self.assertRaisesRegex(ValueError, 'requirement pin'): self.package()
        self.write('server/team_runtime.py', 'import os\n')
        self.write('hospital-ui/app.mjs', "import './missing.mjs';\n")
        with self.assertRaisesRegex(ValueError, 'missing.mjs'): self.package()

    def test_hospital_vm_harness_text_is_not_a_module_dependency(self):
        self.write('test_hospital_vm_fixture.mjs', '''
const rewrite = (text) => text.replaceAll("import('./", "__dynamicImport('./");
import { fixture } from './hospital-ui/app.mjs';
const recover = () => import('./hospital-ui/recovery.mjs');
''')
        self.write('hospital-ui/recovery.mjs', 'export const recover = true;\n')
        output = self.package()
        self.assertTrue((output/'source/test_hospital_vm_fixture.mjs').is_file())
        (self.source/'hospital-ui/recovery.mjs').unlink()
        with self.assertRaisesRegex(ValueError, r'Missing local script/style/module: .*recovery\.mjs'):
            self.package('missing-recovery')

    def test_frozen_source_profile_failure_blocks_copy_before_output_creation(self):
        with patch.object(release, 'source_profile', return_value={'ok':False, 'issues':['changed baseline']}):
            with self.assertRaisesRegex(ValueError, 'Frozen allocation'): self.package()
        self.assertFalse((self.base/'artifact').exists())

    def test_source_change_during_copy_is_preserved_as_failed_snapshot(self):
        original = release.verify_release
        def change_then_verify(output, **kwargs):
            self.write('hospital-ui/app.mjs', '// changed during snapshot\n')
            return original(output, **kwargs)
        with patch.object(release, 'verify_release', side_effect=change_then_verify):
            with self.assertRaisesRegex(ValueError, 'snapshot failed'): self.package()
        report = json.loads((self.base/'artifact/verification.json').read_text(encoding='utf-8'))
        self.assertFalse(report['ok']); self.assertFalse(report['source_stable_during_snapshot'])

    def test_source_symlink_is_rejected(self):
        target = self.base/'outside.mjs'; target.write_text('// outside')
        link = self.source/'hospital-ui/linked.mjs'
        try: link.symlink_to(target)
        except (OSError, NotImplementedError): self.skipTest('OS account cannot create symbolic links.')
        with self.assertRaisesRegex(ValueError, 'Reparse'): self.package()

    def test_allowlist_has_root_launchers_current_ui_tests_and_no_raw_archive(self):
        for name in ('TEAM_START.ps1', 'TEAM_VERIFY.ps1', 'server/team_release_info.py',
                     'test_team_release_info.py', 'test_hospital_workflow.mjs', 'test_paramedic_main_workflow.mjs',
                     'test_patient_workflow_state.mjs', 'paramedic-ui/evaluation-cases.json',
                     'docs/README.md', 'docs/PROJECT_STATUS.md', 'docs/RUNNING.md', 'docs/ARCHITECTURE.md',
                     'docs/ALGORITHMS.md', 'docs/VOICE.md', 'docs/TESTING.md', 'docs/DEMO.md',
                     'docs/FINAL_ALGORITHM_DECISION_20261030.md', 'docs/WAITING_PROTECTION_20261030.md',
                     'docs/API.md', 'docs/VOICE_INPUT_API.md', 'docs/INQUIRY_WORKFLOW.md',
                     'server/flow_console_runtime.py', 'server/voice_recognition_context.py',
                     'hospital-ui/inquiry-layout.css', 'paramedic-ui/field-workflow.mjs',
                     'paramedic-ui/field-workflow.css', 'paramedic-ui/field-entry.mjs',
                     'paramedic-ui/unified-entry.mjs', 'paramedic-ui/critical-conversation.mjs',
                     'paramedic-ui/critical-conversation-audio.mjs',
                     'docs/ALLOCATION_PRESENTATION_EVIDENCE_20261105.md',
                     'docs/RESOURCE_COST_COMPARISON_20261105.html',
                     'docs/VOICE_CURRENT_FEATURES_DEV2_20261103.md',
                     'docs/evidence/20261105/allocation-seed9.json',
                     'docs/evidence/20261105/resource-workload.json',
                     'docs/evidence/20261105/verify.py',
                     'docs/team-handoff/20261105/dev1/README.md',
                     'docs/team-handoff/20261105/dev2/README.md',
                     'docs/team-handoff/20261102/COMMON_RUNTIME.md',
                     'docs/api/SERVER_SCHEMA_20261102.json'):
            self.assertIsNotNone(release.role(name), name)
        for name in ('outputs/raw.gz', '.solver_deps/ortools.py', 'simulation/fixtures/private.json.gz', 'data/snapshot.db', '.env.local',
                     'docs/api/other.json', 'docs/TEAM_RELEASE_20261030.md', 'docs/SERVER_DOMAIN_20261102.md'):
            self.assertIsNone(release.role(name), name)

    def test_latest_runtime_and_fixed_evidence_are_in_snapshot_with_import_closure(self):
        self.write('server/flow_console_runtime.py',
                   'from . import flow_console, voice_recognition_context\n')
        self.write('docs/evidence/20261105/private.json', 'private sentinel')
        names = release.selected_paths(self.source)
        self.assertIn('server.flow_console_runtime', release.ENTRYPOINTS)
        self.assertIn('docs/evidence/20261105/allocation-seed9.json', names)
        self.assertIn('docs/team-handoff/20261105/dev1/README.md', names)
        self.assertNotIn('docs/evidence/20261105/private.json', names)
        closure = release.dependency_check(self.source, names)
        self.assertTrue(closure['ok'], closure)
        self.assertIn('server.flow_console', closure['local_modules'])
        self.assertIn('server.voice_recognition_context', closure['local_modules'])

    def test_missing_latest_feature_or_explicit_evidence_stops_packaging(self):
        for name in ('server/voice_recognition_context.py', 'hospital-ui/inquiry-layout.css',
                     'paramedic-ui/field-workflow.mjs', 'docs/evidence/20261105/manifest.json'):
            with self.subTest(name=name):
                path = self.source/name
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        release.selected_paths(self.source)
                finally:
                    path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()

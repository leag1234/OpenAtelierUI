# SPDX-License-Identifier: BSD-3-Clause
"""Primary regression fixtures for observed synthetic identity-policy migration."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from migrate_sqlite import MigrationError, _parse_identity_configuration, convert, preview
from test_migrate_sqlite import build_source, build_template

SETTINGS = {'ui.enable_signup': False, 'ui.enable_login_form': True,
            'ui.default_user_role': 'pending', 'auth.admin.show': True}

class IdentityConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='openatelier-identity42-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.uploads, self.template = self.root/'source.db', self.root/'uploads', self.root/'template.db'
        build_source(self.source, self.uploads)
        build_template(self.template)
        db=sqlite3.connect(self.source)
        db.executemany('INSERT INTO config(key,value) VALUES (?,?)', [(k,json.dumps(v)) for k,v in SETTINGS.items()])
        db.commit();db.close()
    def mutate(self, sql, args=()):
        db=sqlite3.connect(self.source);db.execute(sql,args);db.commit();db.close()
    def parse(self):
        db=sqlite3.connect(self.source);db.row_factory=sqlite3.Row
        try:return _parse_identity_configuration(db)
        finally:db.close()
    def test_default_off_and_explicit_partial_preview(self):
        self.assertNotIn('identity_configuration',preview(self.source,self.uploads,self.template))
        report=preview(self.source,self.uploads,self.template,include_identity_configuration=True)
        self.assertFalse(report['fatal']);self.assertFalse(report['cutover_eligible'])
        self.assertEqual(report['identity_configuration']['selected_key_count'],4)
        self.assertIn('identity_configuration_prototype',{b['code'] for b in report['blockers']})
    def test_exact_converted_settings_and_originals_retained(self):
        inputs=[self.source,self.template,*self.uploads.iterdir()]
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        target=self.root/'target'
        convert(self.source,self.uploads,self.template,target,include_identity_configuration=True)
        db=sqlite3.connect(target/'webui.db');data=json.loads(db.execute('select data from config').fetchone()[0]);db.close()
        self.assertIs(data['ui']['enable_signup'],False)
        self.assertIs(data['ui']['ENABLE_LOGIN_FORM'],True)
        self.assertEqual(data['ui']['default_user_role'],'pending')
        self.assertIs(data['auth']['admin']['show'],True)
        self.assertNotIn('enable_login_form',data['ui'])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    def test_alternate_supported_boolean_and_user_role(self):
        self.mutate('UPDATE config SET value=? WHERE key=?',('true','ui.enable_signup'))
        self.mutate('UPDATE config SET value=? WHERE key=?',('false','ui.enable_login_form'))
        self.mutate('UPDATE config SET value=? WHERE key=?',('"user"','ui.default_user_role'))
        values=self.parse();self.assertTrue(values['ui.enable_signup']);self.assertFalse(values['ui.enable_login_form']);self.assertEqual(values['ui.default_user_role'],'user')
    def test_wrong_boolean_shapes_and_unsupported_role_refused(self):
        for raw in ['null','0','"false"','[]','{}','true false','"'+('x'*4097)+'"']:
            with self.subTest(raw=raw[:30]):
                self.mutate('UPDATE config SET value=? WHERE key=?',(raw,'ui.enable_signup'))
                with self.assertRaises(MigrationError):self.parse()
        self.mutate('UPDATE config SET value=? WHERE key=?',('false','ui.enable_signup'))
        for raw in ['"admin"','"unknown"','null','false']:
            self.mutate('UPDATE config SET value=? WHERE key=?',(raw,'ui.default_user_role'))
            with self.assertRaises(MigrationError):self.parse()
    def test_missing_duplicate_and_alias_settings_refused(self):
        self.mutate('DELETE FROM config WHERE key=?',('ui.enable_signup',))
        with self.assertRaises(MigrationError):self.parse()
        self.mutate('INSERT INTO config(key,value) VALUES (?,?)',('ui.enable_signup','false'))
        self.mutate('INSERT INTO config(key,value) VALUES (?,?)',('ui.enable_signup','false'))
        with self.assertRaises(MigrationError):self.parse()
    def test_aliases_parent_and_dotted_overrides_refused(self):
        for key in ['ENABLE_SIGNUP','ui.ENABLE_LOGIN_FORM','UI.ENABLE_SIGNUP','ui','auth','auth.admin','ui.enable_signup.extra']:
            self.mutate('INSERT INTO config(key,value) VALUES (?,?)',(key,'false'))
            with self.subTest(key=key),self.assertRaises(MigrationError):self.parse()
            self.mutate('DELETE FROM config WHERE key=?',(key,))
    def test_unselected_mfa_and_secret_settings_stay_unmapped(self):
        self.mutate('INSERT INTO config(key,value) VALUES (?,?)',('auth.mfa.enable','true'))
        self.mutate('INSERT INTO config(key,value) VALUES (?,?)',('oauth.synthetic.secret','"synthetic-unused"'))
        report=preview(self.source,self.uploads,self.template,include_identity_configuration=True)
        self.assertEqual(report['identity_configuration']['remaining_config_rows'],2)
        self.assertFalse(report['cutover_eligible'])
        self.assertEqual(self.parse(),SETTINGS)
    def test_cli_flag_and_coordinator_preview_propagate(self):
        args=['--source-db',str(self.source),'--uploads-root',str(self.uploads),'--empty-old-target-template',str(self.template),'--include-identity-configuration']
        q=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('migrate_sqlite.py')),'preview',*args],capture_output=True,text=True)
        self.assertEqual(q.returncode,3,q.stderr);self.assertEqual(json.loads(q.stdout)['identity_configuration']['selected_key_count'],4)
        q=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('migrate_atlas_copy.py')),'preview',*args,'--workspace-dir',str(self.root/'workspace')],capture_output=True,text=True)
        self.assertEqual(q.returncode,0,q.stderr)
        summary=json.loads(q.stdout);self.assertTrue(summary['ok']);self.assertFalse(summary['cutover'])
        core=json.loads((self.root/'workspace/core-preview.json').read_text())
        self.assertEqual(core['identity_configuration']['selected_key_count'],4)

if __name__=='__main__':unittest.main()

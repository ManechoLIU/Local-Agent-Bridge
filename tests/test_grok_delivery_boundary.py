import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('boundary',Path(__file__).resolve().parents[1] / 'scripts' / 'grok_delivery_boundary.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class BoundaryTests(unittest.TestCase):
    def test_ndjson_preserved_without_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            r=m.execute_preserving_streams([sys.executable,'-c','print(\'{"event":1}\\n{"event":2}\')'],prompt='',cwd=d,base=d,assignment_id='one',timeout=5,env=os.environ.copy())
            self.assertEqual(r['exit_code'],0)
            self.assertEqual(len(Path(r['stdout_path']).read_text().splitlines()),2)
            self.assertEqual(Path(r['stdout_path']).stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                m.execute_preserving_streams([sys.executable,'-c','raise Exception()'],prompt='',cwd=d,base=d,assignment_id='one',timeout=5,env=os.environ.copy())
    def test_timeout_preserves_partial_output(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Exception):
                m.execute_preserving_streams([sys.executable,'-c','import time;print("partial",flush=True);time.sleep(3)'],prompt='',cwd=d,base=d,assignment_id='partial',timeout=.15,env=os.environ.copy())
            self.assertEqual(Path(d,'partial-stdout.log').read_text(),'partial\n')
    def test_exit_zero_and_completed_without_delivery_is_failure(self):
        self.assertEqual(m.classify_delivery(execution={'exit_code':0},terminal={'assignment_id':'a','exit_code':0,'outcome_code':'RESULT_PARSE_FAILED'},lease={'assignment_id':'a','terminal_state':'completed'},delivery=None,assignment_id='a'),'DELIVERY_FAILED')
    def test_pass_and_identity_required(self):
        t={'assignment_id':'a','exit_code':0,'outcome_code':'SUCCESS','delivery_outcome':'pass','phase_history':['FINAL_RESULT']}
        l={'assignment_id':'a','terminal_state':'completed'};d={'delivery_outcome':'pass','evidence':['test-log:x'],'artifacts':['file:x']}
        args=dict(execution={'exit_code':0},terminal=t,lease=l,delivery=d,assignment_id='a')
        self.assertEqual(m.classify_delivery(**args),'DELIVERED_PENDING_PARENT_ACCEPTANCE')
        t['assignment_id']='old'
        self.assertEqual(m.classify_delivery(**args),'RESULT_UNKNOWN')
if __name__=='__main__': unittest.main()

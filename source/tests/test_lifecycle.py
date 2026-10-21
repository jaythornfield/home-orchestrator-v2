"""No-service unit checks of the real lifecycle helper."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tools.portable_launcher import StopControl,classify_port_result,observe_port,verify_package,PreparationCancelled

class LifecycleTests(unittest.TestCase):
    def test_first_stop_reason_is_not_relabelled_by_parent(self):
        control=StopControl(deadline=1)
        control.request('NATURAL_DEADLINE');control.request('OPERATOR_STOP','PIPE')
        self.assertEqual(control.reason,'NATURAL_DEADLINE')

    def test_unknown_reason_is_fixed_safety_failure(self):
        control=StopControl(deadline=1);control.request('SYNTHETIC_UNTRUSTED_TEXT')
        self.assertEqual(control.reason,'SAFETY_CONTROL_FAILURE')

    def test_connection_error_taxonomy(self):
        self.assertEqual(classify_port_result(0),'CONNECTED')
        self.assertEqual(classify_port_result(10061),'CONNECTION_REFUSED')
        self.assertEqual(classify_port_result(10013),'ACCESS_DENIED')
        self.assertEqual(classify_port_result(10060),'TIMEOUT')
        self.assertEqual(classify_port_result(9),'OTHER_ERROR')

    def test_access_denied_is_not_release(self):
        class FakeSocket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def settimeout(self,value):pass
            def connect_ex(self,address):return 10013
        with patch('tools.portable_launcher.socket.socket',return_value=FakeSocket()):
            value=observe_port(12345)
        self.assertEqual((value['classification'],value['actual_errno']),('ACCESS_DENIED',10013))

    def test_actual_preparation_file_verification_can_cancel_before_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'PACKAGE_INTEGRITY.json').write_text(json.dumps({'build_id':'HO2-R6-UNIT',
                'files':[{'path':'not-opened','bytes':0,'sha256':'0'*64}]}),encoding='utf-8')
            control=StopControl(deadline=1);control.request('OPERATOR_STOP','PIPE')
            with patch('tools.portable_launcher.ROOT',root):
                with self.assertRaises(PreparationCancelled):verify_package(control)


    def test_atomic_json_long_local_parent_does_not_repeat_target_basename(self):
        from tools.portable_launcher import atomic_json
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            self.assertLess(len(str(base)),209)
            parent=base/('x'*(210-len(str(base))-1));parent.mkdir()
            target=parent/'ownership.json'
            atomic_json(target,{'synthetic':True})
            self.assertEqual(json.loads(target.read_bytes()),{'synthetic':True})

if __name__=='__main__':unittest.main(verbosity=2)


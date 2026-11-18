"""No-secret TCP probe contract tests, entirely offline."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import shadow_mtproto_tcp_probe as probe


class TelegramNetworkProbeTests(unittest.TestCase):
    def test_success_and_failure_both_close_sockets(self):
        class Fake:
            def __init__(self, failure=False):
                self.failure=failure
                self.closed=False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.closed=True
            def settimeout(self, seconds):
                self.timeout=seconds
            def connect(self, addr):
                self.destination=addr
                if self.failure:
                    raise TimeoutError("synthetic timeout")
        made=[]
        def factory(family, type_):
            s=Fake(failure=family==socket.AF_INET6)
            made.append((family,type_,s))
            return s
        with patch.object(probe.socket,"socket",side_effect=factory):
            self.assertTrue(probe.try_tcp(probe.DC2_IPV4,socket.AF_INET))
            self.assertFalse(probe.try_tcp(probe.DC2_IPV6,socket.AF_INET6))
        self.assertTrue(all(s.closed for _,_,s in made))
        self.assertEqual([x[0] for x in made],[socket.AF_INET,socket.AF_INET6])
        self.assertTrue(all(x[1]==socket.SOCK_STREAM for x in made))
        self.assertTrue(all(x[2].destination[1]==443 for x in made))

    def test_report_does_not_claim_mtproto_or_source(self):
        with patch.object(probe,"try_tcp",side_effect=[True,False]) as check:
            result=probe.probe()
        self.assertEqual(check.call_count,2)
        self.assertTrue(result["tcp443_ipv4"])
        self.assertFalse(result["tcp443_ipv6"])
        self.assertFalse(result["telegram_authenticated"])
        self.assertFalse(result["mtproto_protocol_verified"])
        self.assertFalse(result["telegram_messages_read"])
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["scientific_state"],"not_activated")
        for forbidden in ("TG_SESSION","API_HASH","message_id","telegram_user"):
            self.assertNotIn(forbidden,json.dumps(result))

    def test_timeout_is_bounded(self):
        for timeout in (0,0.49,10.1):
            with self.assertRaises(ValueError):
                probe.try_tcp(probe.DC2_IPV4,socket.AF_INET,timeout)

    def test_existing_report_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"report.json"
            target.write_text("previous",encoding="utf-8")
            with patch.object(probe,"probe",return_value={"ok":True}),patch(
                    "sys.argv",["tcp-probe","--output",str(target)]):
                with self.assertRaises(FileExistsError):
                    probe.main()
            self.assertEqual(target.read_text(encoding="utf-8"),"previous")


if __name__=="__main__":
    unittest.main()

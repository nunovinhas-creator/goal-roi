import asyncio
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError, URLError

from scangoal import reader
from scripts.dispatch_collection import dispatch, TRANSIENT


class DispatchTests(unittest.TestCase):
    def test_transient_then_success(self):
        for failure in [HTTPError('', n, '', {}, None) for n in TRANSIENT] + [TimeoutError(), URLError('DNS')]:
            call = Mock(side_effect=[failure, nullcontext()])
            sleep = Mock()
            dispatch('fixture', call, sleep)
            sleep.assert_called_once_with(2)
            self.assertEqual(call.call_count, 2)

    def test_exhaustion_and_nontransient(self):
        call, sleep = Mock(side_effect=ConnectionResetError()), Mock()
        with self.assertRaises(RuntimeError):
            dispatch('fixture', call, sleep)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8, 16])
        for status, headers in [(403, {}), (429, {'Retry-After': '300'})]:
            call = Mock(side_effect=HTTPError('', status, '', headers, None))
            with self.assertRaises(HTTPError):
                dispatch('fixture', call, sleep)
            self.assertEqual(call.call_count, 1)


class WindowTests(unittest.TestCase):
    def test_gap_restart_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            journal = path / 'messages.jsonl'
            journal.write_text('original')
            state = reader.record_window(path, {'last_run_utc':'2026-11-19T03:00:00+00:00'},
                                         100, 500, {500, 501}, '2026-11-19T07:15:00+00:00')
            with patch.object(reader, 'fetch', AsyncMock(side_effect=TimeoutError())), self.assertRaises(TimeoutError):
                asyncio.run(reader.recover_page(None, None, path, state, 'now'))
            page = ([{'message_id': 499, 'collected_at_utc':'later'}], 400, set(range(400, 500)))
            with patch.object(reader, 'fetch', AsyncMock(return_value=page)) as fetch:
                with patch.object(reader, 'write_json', wraps=reader.write_json) as write:
                    def crash_after_archive(file, value):
                        if file.name == 'collection_windows.json':
                            raise OSError('restart')
                        return original(file, value)
                    original = write._mock_wraps
                    write.side_effect = crash_after_archive
                    with self.assertRaises(OSError):
                        asyncio.run(reader.recover_page(None, None, path, state, 'now'))
                state = json.loads((path/'collection_windows.json').read_text())
                asyncio.run(reader.recover_page(None, None, path, state, 'now'))
                self.assertEqual(fetch.await_count, 1)
            with patch.object(reader, 'fetch', AsyncMock(return_value=([], None, set()))) as fetch:
                asyncio.run(reader.recover_page(None, None, path, state, 'now'))
                self.assertEqual(fetch.await_args.kwargs, {'min_id':100, 'offset_id':400})
                asyncio.run(reader.recover_page(None, None, path, state, 'now'))
                self.assertEqual(fetch.await_count, 1)
            self.assertEqual(journal.read_text(), 'original')
            self.assertEqual(state['intervals'][0]['status'], 'available_history_checked')

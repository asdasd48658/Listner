import os, tempfile, unittest
from listner.db import Store

class MonitorLeaseTests(unittest.TestCase):
    def setUp(self):
        self.file=tempfile.NamedTemporaryFile(delete=False); self.file.close()
        self.db=Store(self.file.name); self.db.initialize()
    def tearDown(self): os.unlink(self.file.name)
    def test_only_one_owner_can_acquire_same_group_call(self):
        self.assertEqual('worker-a', self.db.acquire_monitor(100, 200, 1, owner='worker-a', now=100, lease_seconds=10))
        self.assertIsNone(self.db.acquire_monitor(100, 200, 1, owner='worker-b', now=101, lease_seconds=10))
    def test_expired_lease_can_be_taken_by_another_worker(self):
        self.db.acquire_monitor(100, 200, 1, owner='worker-a', now=100, lease_seconds=10)
        self.assertEqual('worker-b', self.db.acquire_monitor(100, 200, 1, owner='worker-b', now=111, lease_seconds=10))
    def test_lease_is_unique_per_call_not_merely_group(self):
        self.db.acquire_monitor(100, 200, 1, owner='worker-a', now=100)
        self.assertEqual('worker-b', self.db.acquire_monitor(100, 201, 2, owner='worker-b', now=100))
    def test_presence_emits_one_join_and_one_duration_leave(self):
        self.assertEqual(('joined', 0), self.db.record_presence(100, 200, 9, True, '9 in Team', now=100))
        self.assertIsNone(self.db.record_presence(100, 200, 9, True, '9 in Team', now=101))
        self.assertEqual(('left', 25), self.db.record_presence(100, 200, 9, False, '9 in Team', now=125))
        with self.db.connection() as con:
            alerts=con.execute('SELECT kind, body FROM alerts ORDER BY id').fetchall()
        self.assertEqual([('joined', '9 in Team'), ('left', '9 in Team after 25s')], [tuple(x) for x in alerts])
    def test_stale_owner_cannot_renew_after_takeover(self):
        self.db.acquire_monitor(100, 200, 1, owner='a', now=100, lease_seconds=5)
        self.db.acquire_monitor(100, 200, 1, owner='b', now=106, lease_seconds=5)
        self.assertFalse(self.db.renew_monitor(100, 200, 'a', 5, now=106))
        self.assertTrue(self.db.renew_monitor(100, 200, 'b', 5, now=106))
if __name__=='__main__': unittest.main()

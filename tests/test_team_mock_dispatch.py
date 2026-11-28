"""Team Dispatch against the local mock Hark: Dispatch -> Sent -> Reply received, two mock accounts, account switch in
the same Cent profile, duplicate dispatch, and the Plan Preview / Real Hark manual buttons (offscreen Qt).

Isolation: APPDATA / LOCALAPPDATA point to a temp folder before hark_manager is imported, the Manager bridge listens on
a free port, the Telegram token read is stubbed, Cent is never launched (open_in is replaced) and nothing reaches
hark.com. Run from the repo root:  python -m unittest discover -s tests -v"""
import copy
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock as umock

TMP = tempfile.mkdtemp(prefix="hbm-test-")
os.environ["APPDATA"] = os.path.join(TMP, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "Local")
os.makedirs(os.environ["APPDATA"], exist_ok=True)
os.makedirs(os.environ["LOCALAPPDATA"], exist_ok=True)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hark_manager import config, bridge, core, hark_api, repo_team  # noqa: E402
from hark_manager.api_dispatch import ApiDispatcher  # noqa: E402
from hark_manager.chrome import Profile  # noqa: E402
from hark_manager.mock_hark import MockHark  # noqa: E402

if not os.path.normcase(config.APPDATA).startswith(os.path.normcase(TMP)):
    raise RuntimeError(f"test isolation failed: APPDATA is {config.APPDATA}")
bridge.BRIDGE_PORT = 0                  # never the live Manager's bridge port
core.get_bot_token = lambda: ""         # no Telegram token read / no Telegram call

APP = QApplication.instance() or QApplication([])
A, B = "worker.a@mock.hark.local", "worker.b@mock.hark.local"
C = "worker.c@mock.hark.local"
TAG_A, TAG_B = "aaa111", "bbb222"
P1 = "11111111-aaaa-4aaa-8aaa-111111111111"
P2 = "22222222-bbbb-4bbb-8bbb-222222222222"
P3 = "33333333-cccc-4ccc-8ccc-333333333333"
URL = "https://hark.com/project/"
SHOTS = os.environ.get("HBM_TEST_SHOTS", "")


def wait(pred, timeout=10.0, qt=False):
    end = time.time() + timeout
    while time.time() < end:
        if qt:
            APP.processEvents()
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    return pred()


class MockApiTests(unittest.TestCase):
    """ApiDispatcher + HarkApi against MockHark over real HTTP on 127.0.0.1."""

    def setUp(self):
        self.mock = MockHark(reply_delay=0.3)
        self.mock.start()
        self.ua, self.ub = self.mock.add_account(A), self.mock.add_account(B)
        self.mock.add_project(P1, self.ua)
        self.mock.add_project(P2, self.ub)
        self.mock.sign_in(TAG_A, self.ua)
        self.mock.sign_in(TAG_B, self.ub)
        m = self.mock
        self.d = ApiDispatcher(lambda target, tag: hark_api.HarkApi(m.base_url, lambda: m.cookie_header(tag), timeout=3),
                               poll=0.05, confirm_timeout=1.0, reply_timeout=6.0)

    def tearDown(self):
        self.d.shutdown()
        self.mock.stop()

    def final(self, key, d=None):
        d = d or self.d
        return wait(lambda: (lambda x: x if x["final"] else None)(d.record(key)))

    def go(self, tag=TAG_A, acc=A, pid=P1, text="Task one", final=True):
        rec, why = self.d.dispatch("mock", tag, acc, URL + pid, text, alias="@" + tag)
        self.assertIsNotNone(rec, why)
        return self.final(rec["key"]) if final else rec

    def statuses(self, rec):
        return [s for s, _ in self.d.record(rec["key"])["history"]]

    def test_two_accounts_full_flow(self):
        ra = self.go(TAG_A, A, P1, "Task for A", final=False)
        rb = self.go(TAG_B, B, P2, "Task for B", final=False)
        for rec, pid, acc, uid in ((ra, P1, A, self.ua), (rb, P2, B, self.ub)):
            r = self.final(rec["key"])
            self.assertEqual(r["status"], "reply", r["reason"])
            self.assertEqual(self.statuses(r), ["verifying", "sending", "sent", "reply"])
            self.assertEqual(r["reply"], f"MOCK-REPLY project={pid} account={acc}: task received ({len(r['text'])} chars)")
            msgs = self.mock.user_messages(pid)
            self.assertEqual(len(msgs), 1)
            self.assertEqual((msgs[0]["id"], msgs[0]["userId"], msgs[0]["content"]), (r["message_id"], uid, r["text"]))
        self.assertNotEqual(self.d.record(ra["key"])["reply_id"], self.d.record(rb["key"])["reply_id"])

    def test_sent_only_after_confirmed_delivery(self):
        for fault, status in (("http500", "unconfirmed"), ("hide_message", "unconfirmed"),
                              ("wrong_ack_conversation", "unconfirmed"), ("drop_ack", "reply")):
            with self.subTest(fault=fault):
                self.mock.inject_fault(fault, P1)
                before = len(self.mock.user_messages(P1))
                r = self.go(text=f"fault {fault}")
                self.assertEqual(r["status"], status, r["reason"])
                hist = self.statuses(r)
                if status == "unconfirmed":
                    self.assertEqual(hist, ["verifying", "sending", "unconfirmed"])
                else:   # the acknowledgement was lost but the conversation shows the message under our reply id
                    self.assertEqual(hist, ["verifying", "sending", "sent", "reply"])
                time.sleep(0.4)     # nothing is re-sent on its own
                stored = len(self.mock.user_messages(P1)) - before
                self.assertEqual(stored, {"http500": 0, "hide_message": 0, "wrong_ack_conversation": 1, "drop_ack": 1}[fault])

    def test_unconfirmed_resumes_with_same_idempotency_key(self):
        self.mock.inject_fault("http500", P1)
        r1 = self.go(text="resume me")
        self.assertEqual(r1["status"], "unconfirmed")
        r2 = self.go(text="resume me")
        self.assertEqual(r2["status"], "reply", r2["reason"])
        self.assertTrue(r2["resumed"])
        self.assertEqual((r2["idempotency_key"], r2["reply_id"]), (r1["idempotency_key"], r1["reply_id"]))
        self.assertEqual(len(self.mock.user_messages(P1)), 1)
        # hidden message: the resend is answered from Hark's idempotency record, still not visible -> still unconfirmed
        self.mock.inject_fault("hide_message", P1)
        h1 = self.go(text="hidden")
        h2 = self.go(text="hidden")
        self.assertEqual((h1["status"], h2["status"]), ("unconfirmed", "unconfirmed"))
        self.assertEqual(len(self.mock.user_messages(P1)), 1)
        self.assertEqual(len(self.mock.sends), 1)

    def test_blocked_retry_of_maybe_delivered_prompt_keeps_idempotency_key(self):
        orig = hark_api.HarkApi.send

        def send_then_switch(api, *a, **k):
            ack = orig(api, *a, **k)
            self.mock.sign_in(TAG_A, self.ub)      # switched right after the send: delivery is not confirmed
            return ack
        with umock.patch.object(hark_api.HarkApi, "send", send_then_switch):
            r1 = self.go(text="stored once")
        self.assertEqual(r1["status"], "unconfirmed", r1["reason"])
        self.assertEqual(len(self.mock.user_messages(P1)), 1)
        r2 = self.go(text="stored once")            # still on B: blocked before any send
        self.assertEqual(r2["status"], "blocked")
        self.mock.sign_in(TAG_A, self.ua)
        r3 = self.go(text="stored once")
        self.assertEqual(r3["status"], "reply", r3["reason"])
        self.assertTrue(r3["resumed"])
        self.assertEqual((r3["idempotency_key"], r3["reply_id"]), (r1["idempotency_key"], r1["reply_id"]))
        self.assertEqual(len(self.mock.user_messages(P1)), 1)

    def test_account_switch_before_dispatch_blocks_then_retry(self):
        self.mock.sign_in(TAG_A, self.ub)          # same Cent profile, now signed in to B
        r = self.go(text="switched before")
        self.assertEqual(r["status"], "blocked")
        self.assertIn(f"Wrong account: profile mein {B}", r["reason"])
        self.assertEqual(self.statuses(r), ["verifying", "blocked"])
        self.assertEqual((self.mock.user_messages(P1), self.mock.user_messages(P2)), ([], []))
        self.mock.sign_in(TAG_A, self.ua)          # back to A: a blocked dispatch may be tried again
        r2 = self.go(text="switched before")
        self.assertEqual((r2["status"], r2["attempt"], r2["resumed"]), ("reply", 2, False))
        self.assertEqual(len(self.mock.user_messages(P1)), 1)

    def test_account_switch_between_verify_and_send_is_refused(self):
        orig = hark_api.HarkApi.send

        def switch_then_send(api, *a, **k):
            self.mock.sign_in(TAG_A, self.ub)
            return orig(api, *a, **k)
        with umock.patch.object(hark_api.HarkApi, "send", switch_then_send):
            r = self.go(text="race")
        self.assertEqual(r["status"], "blocked")
        self.assertIn("account badal gaya", r["reason"])
        self.assertEqual((self.mock.user_messages(P1), self.mock.user_messages(P2)), ([], []))

    def test_account_switch_after_sent_never_reads_other_account(self):
        self.mock.reply_delay = 1.5
        rec = self.go(text="switch after sent", final=False)
        wait(lambda: self.d.record(rec["key"])["status"] == "sent")
        rid = self.d.record(rec["key"])["reply_id"]
        self.mock.sign_in(TAG_A, self.ub)
        self.mock.inject_message(P2, {"id": rid, "role": "assistant", "content": "DECOY from account B",
                                      "userId": self.ub, "isStreaming": False})
        r = self.final(rec["key"])
        self.assertEqual(r["status"], "account_changed")
        self.assertEqual(r["reply"], "")
        self.assertEqual(self.statuses(r), ["verifying", "sending", "sent", "account_changed"])

    def test_reply_only_from_selected_project(self):
        self.mock.reply_delay = 1.2
        self.mock.add_project(P3, self.ua)
        rec = self.go(text="right project only", final=False)
        wait(lambda: self.d.record(rec["key"])["status"] == "sent")
        rid = self.d.record(rec["key"])["reply_id"]
        self.mock.inject_message(P3, {"id": rid, "role": "assistant", "content": "DECOY other project", "userId": self.ua})
        self.mock.inject_message(P1, {"id": "rsp_other", "role": "assistant", "content": "DECOY other id", "userId": self.ua})
        time.sleep(0.4)
        self.assertEqual(self.d.record(rec["key"])["status"], "sent")     # decoys are not a reply
        r = self.final(rec["key"])
        self.assertEqual(r["status"], "reply")
        self.assertTrue(r["reply"].startswith(f"MOCK-REPLY project={P1} account={A}"), r["reply"])

    def test_duplicate_dispatch_sequential_and_same_project(self):
        rec = self.go(text="only once", final=False)
        again, why = self.d.dispatch("mock", TAG_A, A, URL + P1, "only once", "@a")
        self.assertIsNone(again)
        self.assertIn("Duplicate", why)
        other, why2 = self.d.dispatch("mock", TAG_A, A, URL + P1, "another prompt", "@a")
        self.assertIsNone(other)
        self.assertIn("chal raha hai", why2)
        self.assertEqual(self.final(rec["key"])["status"], "reply")
        third, why3 = self.d.dispatch("mock", TAG_A, A, URL + P1, "only once", "@a")
        self.assertIsNone(third)
        self.assertIn("pehle se reply", why3)
        self.assertEqual(len(self.mock.user_messages(P1)), 1)

    def test_duplicate_dispatch_concurrent(self):
        gate, results = threading.Barrier(8), []

        def hit():
            gate.wait()
            results.append(self.d.dispatch("mock", TAG_A, A, URL + P1, "burst", "@a"))
        ts = [threading.Thread(target=hit) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        ok = [r for r, _ in results if r]
        self.assertEqual(len(ok), 1)
        self.assertEqual(self.final(ok[0]["key"])["status"], "reply")
        self.assertEqual(len(self.mock.user_messages(P1)), 1)
        self.assertEqual(len(self.mock.sends), 1)

    def test_live_api_blocked_without_approved_access(self):
        with self.assertRaises(hark_api.LiveAccessBlocked):
            hark_api.HarkApi("https://hark.com", lambda: {})
        d = ApiDispatcher(lambda target, tag: hark_api.HarkApi("https://hark.com", lambda: {}), poll=0.05)
        rec, _ = d.dispatch("live", TAG_A, A, URL + P1, "never sent", "@a")
        r = self.final(rec["key"], d)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("approved access nahi", r["reason"])

    def test_project_scope_fails_closed(self):
        snap = {"conversations": [{"id": P1, "userId": self.ub, "messages": []}, {"id": P2, "messages": []}]}
        self.assertIsNone(hark_api.project_scope(snap, P1, self.ua))    # same id, other owner
        self.assertIsNone(hark_api.project_scope(snap, P2, self.ub))    # no owner
        self.assertIsNotNone(hark_api.project_scope(snap, P1, self.ub))


class PanelTests(unittest.TestCase):
    """Real Controller + TeamDispatchPanel (offscreen), a real temp git repo with worktrees."""

    @classmethod
    def setUpClass(cls):
        cls.repo = os.path.join(TMP, "DemoRepo")
        os.makedirs(cls.repo)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "test"], ["config", "core.autocrlf", "false"]):
            subprocess.run(["git"] + args, cwd=cls.repo, check=True)
        with open(os.path.join(cls.repo, "README.md"), "w") as f:
            f.write("demo\n")
        subprocess.run(["git", "add", "."], cwd=cls.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cls.repo, check=True)
        core.Controller.MOCK_REPLY_DELAY = 0.5
        cls.ctl = ctl = core.Controller()
        ctl.api.poll, ctl.api.confirm_timeout, ctl.api.reply_timeout = 0.05, 2.0, 8.0
        pa, pb = Profile("hbm:" + TAG_A, "Mock Cent A"), Profile("hbm:" + TAG_B, "Mock Cent B")
        pa.tag, pb.tag = TAG_A, TAG_B
        pb.email = B                    # worker B has no team account: the profile's saved Hark email is used
        ctl.profiles = [pa, pb]
        ctl.by_tag = {TAG_A: pa, TAG_B: pb}
        cls.opened, cls.queued = [], []
        ctl.open_in = lambda p, url=None, **k: cls.opened.append((p.tag, url))
        ctl.bridge.queue_command = lambda tag, cmd: cls.queued.append((tag, cmd))
        team = repo_team.empty_team()
        team.update(repo_path=cls.repo, base_ref="HEAD")
        team["workers"] = [{"profile_id": TAG_A, "alias": "@cent2", "project_url": URL + P1, "account": A},
                           {"profile_id": TAG_B, "alias": "@cent3", "project_url": URL + P2}]
        assert not repo_team.validate(team, {TAG_A, TAG_B})
        cls.team, res = ctl.save_team(team)
        assert all(ok for ok, _ in res.values()), res
        from hark_manager.ui.team_dispatch import TeamDispatchPanel
        from hark_manager.ui.style import build_qss
        cls.panel = TeamDispatchPanel(ctl)
        cls.panel.setStyleSheet(build_qss())
        cls.panel.setFixedSize(370, 1250)      # the main window's right panel: 390 px box, 10 px margins
        cls.panel.show()
        wait(lambda: False, 1.6, qt=True)      # the panel's own first team check (1.2 s timer) has run

    @classmethod
    def tearDownClass(cls):
        cls.panel.close()
        cls.ctl.stop_api()
        cls.ctl.bridge.stop()

    def pump(self, secs):
        wait(lambda: False, secs, qt=True)

    def card(self, alias):
        return self.panel.cards[alias]

    def pill(self, alias):
        return self.card(alias).pill.accessibleName()

    def set_plan(self, text):
        self.panel.paste.setPlainText(text)
        self.panel.t_parse.stop()
        self.panel.parse()
        wait(lambda: all("Checking" not in self.pill(a) for a in ("@cent2", "@cent3")), 10, qt=True)

    def shot(self, name):
        self.pump(0.3)                  # posted LayoutRequests: wrapped labels get their final height
        if SHOTS:
            self.panel.grab().save(os.path.join(SHOTS, name))

    def test_1_real_hark_plan_preview_and_manual_buttons(self):
        p = self.panel
        p.target.setCurrentIndex(p.target.findData("real"))
        self.set_plan("1. @cent2 \u2013 Add Settings validation.\n2. @cent3 \u2013 Run GPS regression tests.")
        w2, w3 = self.team["workers"]
        c2, c3 = self.card("@cent2"), self.card("@cent3")
        self.assertEqual(c2.fields["account"].toolTip(), f"Account: {A} (team)")
        self.assertEqual(c3.fields["account"].toolTip(), f"Account: {B} (Cent profile ka Hark email)")
        self.assertEqual(c2.fields["profile"].toolTip(), f"Cent profile: Mock Cent A [{TAG_A}]")
        self.assertEqual(c2.fields["project"].toolTip(), f"Hark project: {URL + P1}")
        self.assertEqual(c3.fields["project"].toolTip(), f"Hark project: {URL + P2}")
        self.assertEqual(c2.fields["worktree"].toolTip(), f"Git worktree: {w2['worktree']}")
        self.assertEqual(c3.fields["worktree"].toolTip(), f"Git worktree: {w3['worktree']}")
        self.assertEqual(c2.fields["branch"].toolTip(), f"Branch: agent-cent2  \u2022  base {self.team['base_sha'][:10]}")
        self.assertTrue(os.path.isdir(w2["worktree"]) and os.path.isdir(w3["worktree"]))
        self.assertFalse(p.b_send.isVisible())
        self.assertTrue(c2.b_open.isVisible() and c2.b_copy.isVisible() and c2.b_copy.isEnabled())
        self.assertFalse(p.target.model().item(2).isEnabled())
        self.assertIn("BLOCKED", p.live.text())
        self.assertTrue(wait(lambda: "Worktrees Ready" in p.repo.text(), 15, qt=True), p.repo.text())
        self.shot("plan_preview_real.png")
        c2.b_copy.click()
        want = repo_team.build_prompt(self.ctl.team(), w2, "Add Settings validation.")
        self.assertEqual(QApplication.clipboard().text(), want)
        self.assertIn("Copied", self.pill("@cent2"))
        c3.b_open.click()
        self.assertEqual(self.opened[-1], (TAG_B, URL + P2))
        self.assertEqual([c for c in self.queued if c[1].get("type") not in ("verify_tab", "open_task")], [])
        p.dispatch()                    # Real Hark: the Manager never sends
        self.assertEqual(self.ctl.api.records, {})
        self.assertIsNone(self.ctl.mock)

    def test_2_local_mock_dispatch_sent_reply(self):
        p = self.panel
        p.target.setCurrentIndex(p.target.findData("mock"))
        self.set_plan("1. @cent2 \u2013 Add Settings validation.\n2. @cent3 \u2013 Run GPS regression tests.")
        m = self.ctl.mock
        self.assertEqual((m.signed_in(TAG_A)["email"], m.signed_in(TAG_B)["email"]), (A, B))
        self.assertIn(f"Mock session: {A}", self.card("@cent2").mock.accessibleName())
        self.assertEqual(p.ready, 2)
        self.assertTrue(p.b_send.isVisible() and p.b_send.isEnabled())
        seen = {}
        self.ctl.api.update.connect(lambda k, r: seen.setdefault(r["alias"], []).append(r["status"]))
        p.b_send.click()
        p.b_send.click()                # second click while the first is busy: ignored
        p.dispatch()
        ok = wait(lambda: all("Reply received" in self.pill(a) for a in ("@cent2", "@cent3")), 15, qt=True)
        self.assertTrue(ok, (self.pill("@cent2"), self.pill("@cent3")))
        for alias, pid, acc in (("@cent2", P1, A), ("@cent3", P2, B)):
            dedup = [s for i, s in enumerate(seen[alias]) if i == 0 or seen[alias][i - 1] != s]
            self.assertEqual(dedup, ["verifying", "sending", "sent", "reply"])
            self.assertEqual(len(m.user_messages(pid)), 1)
            self.assertIn(f"MOCK-REPLY project={pid} account={acc}", self.card(alias).reply.text())
        self.shot("mock_reply_received.png")
        for alias in ("@cent2", "@cent3"):      # the whole reply is on screen, not clipped by the 370 px panel
            lab = self.card(alias).reply
            r = lab.contentsRect()
            need = lab.fontMetrics().boundingRect(0, 0, r.width(), 10000, Qt.TextWordWrap, lab.text()).height()
            self.assertGreaterEqual(r.height(), need, alias)
        p.b_send.click()                # nothing is Ready any more: the same prompts are never sent again
        self.pump(0.5)
        self.assertEqual(len(m.sends), 2)

    def test_3_mock_account_switch_in_same_profile(self):
        p, m = self.panel, self.ctl.mock_hark()
        p.target.setCurrentIndex(p.target.findData("mock"))
        self.set_plan("1. @cent2 \u2013 Second task after switch.\n2. @cent3 \u2013 Run GPS regression tests.")
        self.card("@cent2").b_switch.click()        # worker.a -> next mock account (worker.b) in Cent profile A
        self.assertEqual(m.signed_in(TAG_A)["email"], B)
        self.assertIn("account mismatch", self.card("@cent2").mock.accessibleName())
        sends = len(m.sends)
        p.b_send.click()
        ok = wait(lambda: "Blocked" in self.pill("@cent2"), 10, qt=True)
        self.assertTrue(ok, self.pill("@cent2"))
        self.assertIn("Wrong account", self.pill("@cent2"))
        self.assertEqual(len(m.sends), sends)
        self.assertEqual(len(m.user_messages(P2)), 1)      # nothing landed in B's project either
        self.shot("mock_account_switch_blocked.png")
        self.ctl.mock_switch_account(TAG_A, A)
        self.pump(0.2)
        self.assertIn("Dispatch = retry", self.pill("@cent2"))
        p.b_send.click()
        ok = wait(lambda: "Reply received" in self.pill("@cent2"), 15, qt=True)
        self.assertTrue(ok, self.pill("@cent2"))
        self.assertEqual(len(m.user_messages(P1)), 2)
        self.assertEqual(len(m.sends), sends + 1)

    def test_4_worker_account_changed_in_edit_team(self):
        p, m = self.panel, self.ctl.mock_hark()
        p.target.setCurrentIndex(p.target.findData("mock"))
        self.ctl.mock_switch_account(TAG_A, self.ctl.MOCK_SPARE)     # switched by hand: a team save keeps it
        team = copy.deepcopy(self.ctl.team())
        team["workers"][1]["account"] = C                            # @cent3: profile email B -> team account C
        _, res = self.ctl.save_team(team)
        self.assertTrue(all(ok for ok, _ in res.values()), res)
        self.assertEqual(m.signed_in(TAG_B)["email"], C)
        self.assertEqual(m.signed_in(TAG_A)["email"], self.ctl.MOCK_SPARE)
        self.ctl.mock_switch_account(TAG_A, A)
        self.set_plan("1. @cent2 \u2013 Task after team edit.\n2. @cent3 \u2013 Task for account C.")
        p.b_send.click()
        ok = wait(lambda: all("Reply received" in self.pill(a) for a in ("@cent2", "@cent3")), 15, qt=True)
        self.assertTrue(ok, (self.pill("@cent2"), self.pill("@cent3")))
        self.assertIn(f"MOCK-REPLY project={P2} account={C}", self.card("@cent3").reply.text())


def tearDownModule():
    for root, _dirs, files in os.walk(TMP):     # git writes its object files read-only; rmtree cannot delete those
        for n in files:
            os.chmod(os.path.join(root, n), stat.S_IWRITE)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for the binding-backed :class:`~evennia.actions.actor.Actor` (I1).

Two halves:

* **Legacy parity** — an actor built with no ``binding`` must resolve
  identity/focus/effective/location/holder exactly as the old puppet view did,
  since nothing attaches bindings until later I1 tasks.
* **Bound behavior** — with a :class:`ControlBinding`, focus follows the durable
  stack (floor = account), and push/pop/collapse emit ``FocusChanged`` one per
  level.
* **Race guard** — a dispatch pins focus + generation at entry; if another
  session pops/collapses the pinned body off the live (DB) stack while a
  ``carry_out`` rule is suspended, the resume aborts the phase.
"""

from dataclasses import dataclass

from twisted.internet.defer import Deferred

from evennia.accounts.models import ControlBinding
from evennia.actions.action import Action
from evennia.actions.actor import Actor, FocusChanged
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.result import PASS
from evennia.actions.rule import rule
from evennia.utils import create
from evennia.utils.test_resources import EvenniaTest


def _sync(d):
    """Extract an already-fired Deferred's result (re-raising on failure)."""
    out = {}
    d.addCallbacks(lambda r: out.__setitem__("r", r), lambda f: out.__setitem__("f", f))
    if "f" in out:
        out["f"].raiseException()
    if "r" not in out:
        raise AssertionError("dispatch Deferred did not fire synchronously")
    return out["r"]


@dataclass
class Poke(Action):
    pass


class SlowWork:
    """Two carry_out rules; the high-priority one suspends on an unfired
    Deferred so a test can mutate the stack mid-dispatch before resuming."""

    def __init__(self, fired, d):
        self.fired = fired
        self.d = d

    @rule(Poke, phase="carry_out", priority=10)
    def work(self, action, actor):
        self.fired.append("work")
        return self.d

    @rule(Poke, phase="carry_out", priority=1)
    def after(self, action, actor):
        self.fired.append("after")
        return PASS


class TestActorLegacyParity(EvenniaTest):
    def test_character_actor_matches_old_puppet_view(self):
        a = Actor(session=self.session, account=self.account, character=self.char1)
        self.assertEqual(a.effective, self.char1)
        self.assertEqual(a.identity, self.char1)
        self.assertEqual(a.focus, self.char1)
        self.assertEqual(a.holder, self.char1)
        self.assertEqual(a.location, self.char1.location)

    def test_ooc_actor_falls_to_account(self):
        a = Actor(session=self.session, account=self.account, character=None)
        self.assertEqual(a.effective, self.account)
        self.assertEqual(a.focus, self.account)
        self.assertIsNone(a.location)


class TestActorBound(EvenniaTest):
    def _bound(self):
        # Fresh, unpuppeted identity: setUp auto-binds self.char1, which would
        # collide on the OneToOne db_identity and start with a non-empty stack.
        self.identity = create.create_object(self.character_typeclass, key="BoundChar")
        b = ControlBinding.objects.create(db_account=self.account, db_identity=self.identity)
        return Actor(session=self.session, account=self.account, binding=b), b

    def _recorder(self):
        """Swap the engine singleton's emit for a list-recorder; return the list."""
        from evennia.actions.engine import engine as _eng

        events = []
        original = _eng.emit
        _eng.emit = lambda ev: events.append(ev) or 0
        self.addCleanup(setattr, _eng, "emit", original)
        return events

    def test_identity_is_binding_identity(self):
        a, _ = self._bound()
        self.assertEqual(a.identity, self.identity)

    def test_focus_floor_is_account(self):
        a, _ = self._bound()
        self.assertEqual(a.focus, self.account)
        self.assertEqual(a.effective, self.account)

    def test_push_makes_body_the_focus(self):
        a, _ = self._bound()
        self._recorder()
        a.push_focus(self.char1)
        self.assertEqual(a.focus, self.char1)

    def test_push_emits_focuschanged(self):
        a, _ = self._bound()
        events = self._recorder()
        a.push_focus(self.char1)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertIsInstance(ev, FocusChanged)
        self.assertEqual(ev.change, "push")
        self.assertEqual(ev.body, self.char1)
        self.assertEqual(ev.focus, self.char1)

    def test_pop_returns_and_emits(self):
        a, _ = self._bound()
        a.push_focus(self.char1)
        events = self._recorder()
        dropped = a.pop_focus()
        self.assertEqual(dropped, self.char1)
        self.assertEqual(a.focus, self.account)
        self.assertEqual([e.change for e in events], ["pop"])

    def test_pop_at_floor_is_noop(self):
        a, _ = self._bound()
        events = self._recorder()
        self.assertIsNone(a.pop_focus())
        self.assertEqual(events, [])

    def test_collapse_emits_one_pop_per_level_top_first(self):
        a, _ = self._bound()
        a.push_focus(self.char1)
        a.push_focus(self.obj1)
        a.push_focus(self.obj2)
        events = self._recorder()
        dropped = a.collapse_focus(self.char1)
        self.assertEqual(dropped, [self.obj2, self.obj1])
        self.assertEqual([e.body for e in events], [self.obj2, self.obj1])
        self.assertTrue(all(e.change == "pop" for e in events))
        self.assertEqual(a.focus, self.char1)

    def test_focus_snapshot_pins_across_mutation(self):
        a, b = self._bound()
        a.push_focus(self.char1)
        a._focus_snapshot = self.char1
        # Another session mutates the live stack...
        b.push(self.obj1)
        # ...but this actor's pinned focus is unchanged.
        self.assertEqual(a.focus, self.char1)

    def test_focuschanged_providers_scope(self):
        a, _ = self._bound()
        a.push_focus(self.char1)
        ev = FocusChanged(actor=a, body=self.char1, change="push", focus=a.focus)
        # providers() scopes are [body, identity, focus]; identity is the
        # binding's durable self, distinct from the pushed body/focus here.
        self.assertEqual(ev.providers(), [self.char1, self.identity, self.char1])


class TestFocusRaceGuard(EvenniaTest):
    """The multi-session race guard: a dispatch pins focus at entry; if another
    session pops that body mid-suspend, the resume aborts the carry_out phase."""

    def _setup(self):
        # Fresh, unpuppeted identity (setUp auto-binds self.char1); char1 is
        # still used below as the pushed *body* whose pop triggers the guard.
        identity = create.create_object(self.character_typeclass, key="RaceChar")
        b = ControlBinding.objects.create(db_account=self.account, db_identity=identity)
        b.push(self.char1)  # focus = char1
        actor = Actor(session=self.session, account=self.account, binding=b)
        fired, d = [], Deferred()
        ctx = ActionContext(providers=[SlowWork(fired, d)])
        return b, actor, fired, d, ctx

    def test_pop_during_suspend_aborts_phase(self):
        b, actor, fired, d, ctx = self._setup()
        dd = RuleEngine().dispatch(Poke(), actor, ctx)
        self.assertFalse(dd.called)  # suspended on the unfired Deferred
        self.assertEqual(fired, ["work"])
        # Another session (a second instance of the same row) pops char1.
        other = ControlBinding.objects.get(pk=b.pk)
        self.assertEqual(other.pop(), self.char1)
        d.callback(PASS)  # resume
        self.assertEqual(fired, ["work"])  # 'after' never ran — phase aborted
        self.assertEqual(_sync(dd).outcome, "aborted")

    def test_untouched_focus_completes(self):
        _, actor, fired, d, ctx = self._setup()
        dd = RuleEngine().dispatch(Poke(), actor, ctx)
        d.callback(PASS)
        self.assertEqual(fired, ["work", "after"])
        self.assertEqual(_sync(dd).outcome, "succeeded")

    def test_deeper_push_does_not_abort(self):
        b, actor, fired, d, ctx = self._setup()
        dd = RuleEngine().dispatch(Poke(), actor, ctx)
        # A deeper push bumps generation but leaves char1 on the stack.
        ControlBinding.objects.get(pk=b.pk).push(self.obj1)
        d.callback(PASS)
        self.assertEqual(fired, ["work", "after"])
        self.assertEqual(_sync(dd).outcome, "succeeded")

    def test_unbound_actor_never_aborts(self):
        fired, d = [], Deferred()
        actor = Actor(session=self.session, account=self.account, character=self.char1)
        dd = RuleEngine().dispatch(Poke(), actor, ActionContext(providers=[SlowWork(fired, d)]))
        d.callback(PASS)
        self.assertEqual(fired, ["work", "after"])
        self.assertEqual(_sync(dd).outcome, "succeeded")

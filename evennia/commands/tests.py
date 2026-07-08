"""
Unit testing for the Command system itself.

"""

from unittest.mock import MagicMock, patch

from django.test import override_settings

import evennia
from evennia.commands import cmdparser
from evennia.commands.cmdset import CmdSet
from evennia.commands.command import Command
from evennia.utils.test_resources import BaseEvenniaCommandTest, BaseEvenniaTest, TestCase

# Testing-command sets


class _BaseCmd(Command):
    def __init__(self, cmdset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_cmdset = cmdset


class _CmdA(_BaseCmd):
    key = "A"


class _CmdB(_BaseCmd):
    key = "B"


class _CmdC(_BaseCmd):
    key = "C"


class _CmdD(_BaseCmd):
    key = "D"


class _CmdEe(_BaseCmd):
    key = "E"
    aliases = ["ee"]


class _CmdEf(_BaseCmd):
    key = "E"
    aliases = ["ff"]


class _CmdSetA(CmdSet):
    key = "A"

    def at_cmdset_creation(self):
        self.add(_CmdA("A"))
        self.add(_CmdB("A"))
        self.add(_CmdC("A"))
        self.add(_CmdD("A"))


class _CmdSetB(CmdSet):
    key = "B"

    def at_cmdset_creation(self):
        self.add(_CmdA("B"))
        self.add(_CmdB("B"))
        self.add(_CmdC("B"))


class _CmdSetC(CmdSet):
    key = "C"

    def at_cmdset_creation(self):
        self.add(_CmdA("C"))
        self.add(_CmdB("C"))


class _CmdSetD(CmdSet):
    key = "D"

    def at_cmdset_creation(self):
        self.add(_CmdA("D"))
        self.add(_CmdB("D"))
        self.add(_CmdC("D"))
        self.add(_CmdD("D"))


class _CmdSetEe_Ef(CmdSet):
    key = "Ee_Ef"

    def at_cmdset_creation(self):
        self.add(_CmdEe("Ee"))
        self.add(_CmdEf("Ee"))


# testing Command Sets


class TestCmdSetMergers(TestCase):
    "Test merging of cmdsets"

    def setUp(self):
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()

    def test_union(self):
        a, c = self.cmdset_a, self.cmdset_c
        cmdset_f = a + c  # same-prio
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 2)
        cmdset_f = c + a  # same-prio, inverse order
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)
        a.priority = 1
        cmdset_f = a + c  # high prio A
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)

    def test_intersect(self):
        a, c = self.cmdset_a, self.cmdset_c
        a.mergetype = "Intersect"
        cmdset_f = a + c  # same-prio - c's Union kicks in
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 2)
        cmdset_f = c + a  # same-prio - a's Intersect kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)
        a.priority = 1
        cmdset_f = a + c  # high prio A, intersect kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)

    def test_replace(self):
        a, c = self.cmdset_a, self.cmdset_c
        c.mergetype = "Replace"
        cmdset_f = a + c  # same-prio. C's Replace kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 0)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 2)
        cmdset_f = c + a  # same-prio. A's Union kicks in
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)
        c.priority = 1
        cmdset_f = c + a  # c higher prio. C's Replace kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 0)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 2)

    def test_remove(self):
        a, c = self.cmdset_a, self.cmdset_c
        c.mergetype = "Remove"
        cmdset_f = a + c  # same-prio. C's Remove kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)
        cmdset_f = c + a  # same-prio. A's Union kicks in
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 4)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)
        c.priority = 1
        cmdset_f = c + a  # c higher prio. C's Remove kicks in
        self.assertEqual(len(cmdset_f.commands), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "A"), 2)
        self.assertEqual(sum(1 for cmd in cmdset_f.commands if cmd.from_cmdset == "C"), 0)

    def test_system_cmds_not_duplicated_after_replace(self):
        """System commands must appear exactly once after a Replace merge."""
        a, c = self.cmdset_a, self.cmdset_c

        class _SysCmd(_BaseCmd):
            key = "__sys"

        sys_cmd = _SysCmd("A")
        a.add(sys_cmd)

        c.mergetype = "Replace"
        c.priority = 1
        cmdset_f = c + a  # c higher prio, Replace kicks in

        sys_cmds_in_commands = [cmd for cmd in cmdset_f.commands if cmd.key.startswith("__")]
        self.assertEqual(len(sys_cmds_in_commands), 1)

    def test_system_cmds_not_duplicated_after_union(self):
        """System commands must appear exactly once after a Union merge, from either side."""
        a, c = self.cmdset_a, self.cmdset_c

        class _SysCmd(_BaseCmd):
            key = "__sys"

        # System command on the higher-priority side (cmdset_a)
        a.add(_SysCmd("A"))
        a.priority = 1
        cmdset_f = a + c
        sys_in_commands = [cmd for cmd in cmdset_f.commands if cmd.key.startswith("__")]
        self.assertEqual(len(sys_in_commands), 1)

        # System command on the lower-priority side (cmdset_c)
        a2, c2 = self.cmdset_a, _CmdSetC()
        c2.add(_SysCmd("C"))
        a2.priority = 1
        cmdset_f2 = a2 + c2
        sys_in_commands2 = [cmd for cmd in cmdset_f2.commands if cmd.key.startswith("__")]
        self.assertEqual(len(sys_in_commands2), 1)

    def test_order(self):
        "Merge in reverse- and forward orders, same priorities"
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = d + c + b + a  # merge in reverse order of priority
        self.assertEqual(cmdset_f.priority, 0)
        self.assertEqual(cmdset_f.mergetype, "Union")
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertTrue(all(True for cmd in cmdset_f.commands if cmd.from_cmdset == "A"))
        cmdset_f = a + b + c + d  # merge in order of priority
        self.assertEqual(cmdset_f.priority, 0)
        self.assertEqual(cmdset_f.mergetype, "Union")
        self.assertEqual(len(cmdset_f.commands), 4)  # duplicates setting from A transfers
        self.assertTrue(all(True for cmd in cmdset_f.commands if cmd.from_cmdset == "D"))

    def test_priority_order(self):
        "Merge in reverse- and forward order with well-defined prioritities"
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        cmdset_f = d + c + b + a  # merge in reverse order of priority
        self.assertEqual(cmdset_f.priority, 2)
        self.assertEqual(cmdset_f.mergetype, "Union")
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertTrue(all(True for cmd in cmdset_f.commands if cmd.from_cmdset == "A"))
        cmdset_f = a + b + c + d  # merge in order of priority
        self.assertEqual(cmdset_f.priority, 2)
        self.assertEqual(cmdset_f.mergetype, "Union")
        self.assertEqual(len(cmdset_f.commands), 4)
        self.assertTrue(all(True for cmd in cmdset_f.commands if cmd.from_cmdset == "A"))


class TestOptionTransferTrue(TestCase):
    """
    Test cmdset-merge transfer of the cmdset-special options
    (no_exits/channels/objs/duplicates etc)

    cmdset A has all True options

    """

    def setUp(self):
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()
        self.cmdset_a.priority = 0
        self.cmdset_b.priority = 0
        self.cmdset_c.priority = 0
        self.cmdset_d.priority = 0
        self.cmdset_a.no_exits = True
        self.cmdset_a.no_objs = True
        self.cmdset_a.no_channels = True
        self.cmdset_a.duplicates = True

    def test_option_transfer__reverse_sameprio_passthrough(self):
        """
        A has all True options, merges last (normal reverse merge), same prio.
        The options should pass through to F since none of the other cmdsets
        care to change the setting from their default None.

        Since A.duplicates = True, the final result is an union of duplicate
        pairs (8 commands total).

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = d + c + b + a  # reverse, same-prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 8)

    def test_option_transfer__forward_sameprio_passthrough(self):
        """
        A has all True options, merges first (forward merge), same prio. This
        should pass those options through since the other all have options set
        to None. The exception is `duplicates` since that is determined by
        the two last mergers in the chain both being True.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = a + b + c + d  # forward, same-prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_highprio_passthrough(self):
        """
        A has all True options, merges last (normal reverse  merge) with the
        highest prio. This should also pass through.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        cmdset_f = d + c + b + a  # reverse, A top priority
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_highprio_passthrough(self):
        """
        A has all True options, merges first (forward merge). This is a bit
        synthetic since it will never happen in practice, but logic should
        still make it pass through.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        cmdset_f = a + b + c + d  # forward, A top priority. This never happens in practice.
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_lowprio_passthrough(self):
        """
        A has all True options, merges last (normal reverse merge) with the lowest
        prio. This never happens (it would always merge first) but logic should hold
        and pass through since the other cmdsets have None.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        cmdset_f = d + c + b + a  # reverse, A low prio. This never happens in practice.
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_passthrough(self):
        """
        A has all True options, merges first (forward merge) with lowest prio. This
        is the normal behavior for a low-prio cmdset. Passthrough should happen.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_highprio_block_passthrough(self):
        """
        A has all True options, other cmdsets has False. A merges last with high
        prio. A should retain its option values and override the others

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        c.no_exits = False
        b.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, high prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_highprio_block_passthrough(self):
        """
        A has all True options, other cmdsets has False. A merges last with high
        prio. This situation should never happen, but logic should hold - the highest
        prio's options should survive the merge process.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        c.no_exits = False
        b.no_channels = False
        b.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, high prio, never happens
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_block(self):
        """
        A has all True options, other cmdsets has False. A merges last with low
        prio. This should result in its values being blocked and come out False.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = False
        c.no_channels = False
        b.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_block_partial(self):
        """
        A has all True options, other cmdsets has False excet C which has a None
        for `no_channels`. A merges last with low
        prio. This should result in its values being blocked and come out False
        except for no_channels which passes through.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = False
        c.no_channels = None  # passthrough
        b.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_highprio_sameprio_order_last(self):
        """
        A has all True options and highest prio, D has False and lowest prio,
        others are passthrough. B has the same prio as A, with passthrough.

        Since A is merged last, this should give prio to A's options
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 2
        c.priority = 0
        d.priority = -1
        d.no_channels = False
        d.no_exits = False
        d.no_objs = None
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, A same prio, merged after b
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 8)

    def test_option_transfer__reverse_highprio_sameprio_order_first(self):
        """
        A has all True options and highest prio, D has False and lowest prio,
        others are passthrough. B has the same prio as A, with passthrough.

        While B, with None-values, is merged after A, A's options should have
        replaced those of D at that point, and since B has passthrough the
        final result should contain A's True options.

        Note that despite A having duplicates=True, there is no duplication in
        the DB + A merger since they have different priorities.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 2
        c.priority = 0
        d.priority = -1
        d.no_channels = False
        d.no_exits = False
        d.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + a + b  # reverse, A same prio, merged before b
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_lowprio_block(self):
        """
        A has all True options, other cmdsets has False. A merges last with low
        prio. This usually doesn't happen- it should merge last. But logic should
        hold and the low-prio cmdset's values should be blocked and come out False.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = False
        d.no_channels = False
        b.no_objs = False
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, A low prio, never happens
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)


class TestOptionTransferFalse(TestCase):
    """
    Test cmdset-merge transfer of the cmdset-special options
    (no_exits/channels/objs/duplicates etc)

    cmdset A has all False options

    """

    def setUp(self):
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()
        self.cmdset_a.priority = 0
        self.cmdset_b.priority = 0
        self.cmdset_c.priority = 0
        self.cmdset_d.priority = 0
        self.cmdset_a.no_exits = False
        self.cmdset_a.no_objs = False
        self.cmdset_a.no_channels = False
        self.cmdset_a.duplicates = False

    def test_option_transfer__reverse_sameprio_passthrough(self):
        """
        A has all False options, merges last (normal reverse merge), same prio.
        The options should pass through to F since none of the other cmdsets
        care to change the setting from their default None.

        Since A has duplicates=False, the result is a unique union of 4 cmds.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = d + c + b + a  # reverse, same-prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_sameprio_passthrough(self):
        """
        A has all False options, merges first (forward merge), same prio. This
        should pass those options through since the other all have options set
        to None. The exception is `duplicates` since that is determined by
        the two last mergers in the chain both being .

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = a + b + c + d  # forward, same-prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_highprio_passthrough(self):
        """
        A has all False options, merges last (normal reverse  merge) with the
        highest prio. This should also pass through.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        cmdset_f = d + c + b + a  # reverse, A top priority
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_highprio_passthrough(self):
        """
        A has all False options, merges first (forward merge). This is a bit
        synthetic since it will never happen in practice, but logic should
        still make it pass through.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        cmdset_f = a + b + c + d  # forward, A top priority. This never happens in practice.
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_lowprio_passthrough(self):
        """
        A has all False options, merges last (normal reverse merge) with the lowest
        prio. This never happens (it would always merge first) but logic should hold
        and pass through since the other cmdsets have None.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        cmdset_f = d + c + b + a  # reverse, A low prio. This never happens in practice.
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_passthrough(self):
        """
        A has all False options, merges first (forward merge) with lowest prio. This
        is the normal behavior for a low-prio cmdset. Passthrough should happen.
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_highprio_block_passthrough(self):
        """
        A has all False options, other cmdsets has True. A merges last with high
        prio. A should retain its option values and override the others

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        c.no_exits = True
        b.no_objs = True
        d.duplicates = True
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, high prio
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_highprio_block_passthrough(self):
        """
        A has all False options, other cmdsets has True. A merges last with high
        prio. This situation should never happen, but logic should hold - the highest
        prio's options should survive the merge process.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 1
        c.priority = 0
        d.priority = -1
        c.no_exits = True
        b.no_channels = True
        b.no_objs = True
        d.duplicates = True
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, high prio, never happens
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_block(self):
        """
        A has all False options, other cmdsets has True. A merges last with low
        prio. This should result in its values being blocked and come out False.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = True
        c.no_channels = True
        b.no_objs = True
        d.duplicates = True
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__forward_lowprio_block_partial(self):
        """
        A has all False options, other cmdsets has True excet C which has a None
        for `no_channels`. A merges last with low
        prio. This should result in its values being blocked and come out True
        except for no_channels which passes through.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = True
        c.no_channels = None  # passthrough
        b.no_objs = True
        d.duplicates = True
        # higher-prio sets will change the option up the chain
        cmdset_f = a + b + c + d  # forward, A low prio
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_sameprio_order_last(self):
        """
        A has all False options and highest prio, D has True and lowest prio,
        others are passthrough. B has the same prio as A, with passthrough.

        Since A is merged last, this should give prio to A's False options
        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 2
        c.priority = 0
        d.priority = -1
        d.no_channels = True
        d.no_exits = True
        d.no_objs = True
        d.duplicates = False
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, A high prio, merged after b
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_sameprio_order_first(self):
        """
        A has all False options and highest prio, D has True and lowest prio,
        others are passthrough. B has the same prio as A, with passthrough.

        While B, with None-values, is merged after A, A's options should have
        replaced those of D at that point, and since B has passthrough the
        final result should contain A's False options.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 2
        c.priority = 0
        d.priority = -1
        d.no_channels = True
        d.no_exits = True
        d.no_objs = True
        d.duplicates = False

        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + a + b  # reverse, A high prio, merged before b
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)

    def test_option_transfer__reverse_lowprio_block(self):
        """
        A has all False options, other cmdsets has True. A merges last with low
        prio. This usually doesn't happen- it should merge last. But logic should
        hold and the low-prio cmdset's values should be blocked and come out True.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = -1
        b.priority = 0
        c.priority = 1
        d.priority = 2
        c.no_exits = True
        d.no_channels = True
        b.no_objs = True
        d.duplicates = True
        # higher-prio sets will change the option up the chain
        cmdset_f = d + c + b + a  # reverse, A low prio, never happens
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)


class TestDuplicateBehavior(TestCase):
    """
    Test behavior of .duplicate option, which is a bit special in that it
    doesn't propagate.

    `A.duplicates=True` for all tests.

    """

    def setUp(self):
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()
        self.cmdset_a.priority = 0
        self.cmdset_b.priority = 0
        self.cmdset_c.priority = 0
        self.cmdset_d.priority = 0
        self.cmdset_a.duplicates = True

    def test_reverse_sameprio_duplicate__implicit(self):
        """
        Test of `duplicates` transfer which does not propagate. Only
        A has duplicates=True.

        D + B = DB (no duplication, DB.duplication=None)
        DB + C = DBC  (no duplication, DBC.duplication=None)
        DBC + A = final (duplication, final.duplication=None)

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        cmdset_f = d + b + c + a  # two last mergers duplicates=True
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 8)

    def test_reverse_sameprio_duplicate__explicit(self):
        """
        Test of `duplicates` transfer, which does not propagate.
        C.duplication=True

        D + B = DB (no duplication, DB.duplication=None)
        DB + C = DBC  (duplication, DBC.duplication=None)
        DBC + A = final (duplication, final.duplication=None)

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        c.duplicates = True
        cmdset_f = d + b + c + a  # two last mergers duplicates=True
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 10)

    def test_forward_sameprio_duplicate(self):
        """
        Test of `duplicates` transfer which does not propagate.
        C.duplication=True, merges later than A

        D + B = DB (no duplication, DB.duplication=None)
        DB + A = DBA (duplication, DBA.duplication=None)
        DBA + C = final (duplication, final.duplication=None)

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        c.duplicates = True
        cmdset_f = d + b + a + c  # two last mergers duplicates=True
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 10)

    def test_reverse_sameprio_duplicate_reverse(self):
        """
        Test of `duplicates` transfer which does not propagate.
        C.duplication=False (explicit), merges before A. This behavior is the
        same as if C.duplication=None, since A merges later and takes
        precedence.

        D + B = DB (no duplication, DB.duplication=None)
        DB + C = DBC  (no duplication, DBC.duplication=None)
        DBC + A = final (duplication, final.duplication=None)

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        c.duplicates = False
        cmdset_f = d + b + c + a  # a merges last, takes precedence
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 8)

    def test_reverse_sameprio_duplicate_forward(self):
        """
        Test of `duplicates` transfer which does not propagate.
        C.duplication=False (explicit), merges after A. This just means
        only A causes duplicates, earlier in the chain.

        D + B = DB (no duplication, DB.duplication=None)
        DB + A = DBA (duplication, DBA.duplication=None)
        DBA + C = final (no duplication, final.duplication=None)

        Note that DBA has 8 cmds due to A merging onto DB with duplication,
        but since C merges onto this with no duplication, the union will hold
        6 commands, since C has two commands that replaces the 4 duplicates
        with uniques copies from C.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        c.duplicates = False
        cmdset_f = d + b + a + c  # a merges before c
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 6)


class TestOptionTransferReplace(TestCase):
    """
    Test option transfer through more complex merge types.
    """

    def setUp(self):
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()
        self.cmdset_a.priority = 0
        self.cmdset_b.priority = 0
        self.cmdset_c.priority = 0
        self.cmdset_d.priority = 0
        self.cmdset_a.no_exits = True
        self.cmdset_a.no_objs = True
        self.cmdset_a.no_channels = True
        self.cmdset_a.duplicates = True

    def test_option_transfer__replace_reverse_highprio(self):
        """
        A has all options True and highest priority. C has them False and is
        Replace-type.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.priority = 2
        b.priority = 2
        c.priority = 0
        c.mergetype = "Replace"
        c.no_channels = False
        c.no_exits = False
        c.no_objs = False
        c.duplicates = False
        d.priority = -1

        cmdset_f = d + c + b + a  # reverse, A high prio, C Replace
        self.assertTrue(cmdset_f.no_exits)
        self.assertTrue(cmdset_f.no_objs)
        self.assertTrue(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 7)

    def test_option_transfer__replace_reverse_highprio_from_false(self):
        """
        Inverse of previous test: A has all options False and highest priority.
        C has them True and is Replace-type.

        """
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.no_exits = False
        a.no_objs = False
        a.no_channels = False
        a.duplicates = False

        a.priority = 2
        b.priority = 2
        c.priority = 0
        c.mergetype = "Replace"
        c.no_channels = True
        c.no_exits = True
        c.no_objs = True
        c.duplicates = True
        d.priority = -1

        cmdset_f = d + c + b + a  # reverse, A high prio, C Replace
        self.assertFalse(cmdset_f.no_exits)
        self.assertFalse(cmdset_f.no_objs)
        self.assertFalse(cmdset_f.no_channels)
        self.assertIsNone(cmdset_f.duplicates)
        self.assertEqual(len(cmdset_f.commands), 4)


# test cmdhandler functions


import sys

from twisted.internet.defer import ensureDeferred
from twisted.trial.unittest import TestCase as TwistedTestCase

from evennia.commands import cmdhandler


def _mockdelay(time, func, *args, **kwargs):
    return func(*args, **kwargs)


class TestGetAndMergeCmdSets(TwistedTestCase, BaseEvenniaTest):
    "Test the cmdhandler.get_and_merge_cmdsets function."

    def setUp(self):
        self.patch(sys.modules["evennia.server.sessionhandler"], "delay", _mockdelay)
        super().setUp()
        self.cmdset_a = _CmdSetA()
        self.cmdset_b = _CmdSetB()
        self.cmdset_c = _CmdSetC()
        self.cmdset_d = _CmdSetD()

    def set_cmdsets(self, obj, *args):
        "Set cmdets on obj in the order given in *args"
        for cmdset in args:
            obj.cmdset.add(cmdset)

    def test_from_session(self):
        a = self.cmdset_a
        a.no_channels = True
        self.set_cmdsets(self.session, a)
        (
            command_objects,
            command_objects_list,
            command_objects_list_error,
            caller,
            error_to,
        ) = cmdhandler.generate_cmdset_providers(self.session)

        deferred = ensureDeferred(
            cmdhandler.get_and_merge_cmdsets(self.session, [self.session], "session", "", error_to)
        )

        def _callback(cmdset):
            self.assertEqual(cmdset.key, "A")

        deferred.addCallback(_callback)
        return deferred

    def test_from_account(self):
        from evennia.commands.default.cmdset_account import AccountCmdSet

        a = self.cmdset_a
        a.no_channels = True
        self.set_cmdsets(self.account, a)
        (
            command_objects,
            command_objects_list,
            command_objects_list_error,
            caller,
            error_to,
        ) = cmdhandler.generate_cmdset_providers(self.account)

        deferred = ensureDeferred(
            cmdhandler.get_and_merge_cmdsets(
                self.account, command_objects_list, "account", "", error_to
            )
        )
        # get_and_merge_cmdsets converts  to lower-case internally.

        def _callback(cmdset):
            pcmdset = AccountCmdSet()
            pcmdset.at_cmdset_creation()
            pcmds = [cmd.key for cmd in pcmdset.commands] + ["a", "b", "c", "d"]
            self.assertEqual(set(cmd.key for cmd in cmdset.commands), set(pcmds))

        # _callback = lambda cmdset: self.assertEqual(sum(1 for cmd in cmdset.commands if cmd.key in ("a", "b", "c", "d")), 4)
        deferred.addCallback(_callback)
        return deferred

    def test_from_object(self):
        self.set_cmdsets(self.obj1, self.cmdset_a)
        (
            command_objects,
            command_objects_list,
            command_objects_list_error,
            caller,
            error_to,
        ) = cmdhandler.generate_cmdset_providers(self.obj1)

        deferred = ensureDeferred(
            cmdhandler.get_and_merge_cmdsets(
                self.obj1, command_objects_list, "object", "", error_to
            )
        )
        # get_and_merge_cmdsets converts  to lower-case internally.

        def _callback(cmdset):
            return self.assertEqual(
                sum(1 for cmd in cmdset.commands if cmd.key in ("a", "b", "c", "d")), 4
            )

        deferred.addCallback(_callback)
        return deferred

    def test_multimerge(self):
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.no_exits = True
        a.no_channels = True
        self.set_cmdsets(self.obj1, a, b, c, d)
        (
            command_objects,
            command_objects_list,
            command_objects_list_error,
            caller,
            error_to,
        ) = cmdhandler.generate_cmdset_providers(self.obj1)
        deferred = ensureDeferred(
            cmdhandler.get_and_merge_cmdsets(
                self.obj1, command_objects_list, "object", "", error_to
            )
        )

        def _callback(cmdset):
            self.assertTrue(cmdset.no_exits)
            self.assertTrue(cmdset.no_channels)
            self.assertEqual(cmdset.key, "D")

        deferred.addCallback(_callback)
        return deferred

    def test_duplicates(self):
        a, b, c, d = self.cmdset_a, self.cmdset_b, self.cmdset_c, self.cmdset_d
        a.no_exits = True
        a.no_channels = True
        b.duplicates = True
        d.duplicates = True
        self.set_cmdsets(self.obj1, a, b, c, d)
        (
            command_objects,
            command_objects_list,
            command_objects_list_error,
            caller,
            error_to,
        ) = cmdhandler.generate_cmdset_providers(self.obj1, session=None)

        deferred = ensureDeferred(
            cmdhandler.get_and_merge_cmdsets(
                self.obj1, command_objects_list, "object", "", error_to
            )
        )

        def _callback(cmdset):
            self.assertEqual(len(cmdset.commands), 9)

        deferred.addCallback(_callback)
        return deferred

    def test_command_replace_different_aliases(self):
        cmdset_ee = _CmdSetEe_Ef()
        self.assertEqual(len(cmdset_ee.commands), 1)
        self.assertEqual(cmdset_ee.commands[0].key, "e")


class AccessableCommand(Command):
    def access(*args, **kwargs):
        return True


class _CmdTest1(AccessableCommand):
    key = "test1"
    arg_regex = None


class _CmdTest2(AccessableCommand):
    key = "another command"
    arg_regex = None


class _CmdTest3(AccessableCommand):
    key = "&the third command"
    arg_regex = None


class _CmdTest4(AccessableCommand):
    key = "test2"
    arg_regex = None


class _CmdSetTest(CmdSet):
    key = "test_cmdset"

    def at_cmdset_creation(self):
        self.add(_CmdTest1)
        self.add(_CmdTest2)
        self.add(_CmdTest3)


class TestCmdParser(TestCase):
    def test_create_match(self):
        class DummyCmd:
            pass

        dummy = DummyCmd()

        self.assertEqual(
            cmdparser.create_match("look at", "look at target", dummy, "look"),
            ("look at", " target", dummy, 7, 0.5, "look"),
        )

    @patch.object(cmdparser, "log_trace")
    def test_build_matches_masks_sensitive_input_on_error(self, mock_log_trace):
        class _BrokenCmdSet:
            def __iter__(self):
                raise RuntimeError("forced parser failure")

        cmdparser.build_matches("connect johnny password123", _BrokenCmdSet())
        self.assertTrue(mock_log_trace.called)

        logged = mock_log_trace.call_args[0][0]
        self.assertIn("connect johnny ***********", logged)
        self.assertNotIn("password123", logged)

    def test_build_matches(self):
        """Token-boundary matching (since +underspire.8).

        Prefix characters are load-bearing parts of the key with no
        parse-time stripping.
        """
        a_cmdset = _CmdSetTest()

        # Plain key matches verbatim.
        bcmd = [cmd for cmd in a_cmdset.commands if cmd.key == "test1"][0]
        self.assertEqual(
            cmdparser.build_matches("test1 rock", a_cmdset),
            [("test1", " rock", bcmd, 5, 0.5, "test1")],
        )

        # `@another command ...` does NOT match `another command` — the
        # @ is no longer stripped at parse time.
        self.assertEqual(
            cmdparser.build_matches("@another command smiles to me  ", a_cmdset),
            [],
        )

        # ...but the unprefixed input still matches the unprefixed key.
        bcmd = [cmd for cmd in a_cmdset.commands if cmd.key == "another command"][0]
        self.assertEqual(
            cmdparser.build_matches("another command smiles to me  ", a_cmdset),
            [("another command", " smiles to me  ", bcmd, 15, 0.5, "another command")],
        )

        # Conversely, a `&`-keyed command requires the `&` in the input;
        # plain `the third command` no longer reaches it.
        self.assertEqual(
            cmdparser.build_matches("the third command", a_cmdset),
            [],
        )
        bcmd = [cmd for cmd in a_cmdset.commands if cmd.key == "&the third command"][0]
        self.assertEqual(
            cmdparser.build_matches("&the third command", a_cmdset),
            [("&the third command", "", bcmd, 18, 1.0, "&the third command")],
        )

    def test_num_differentiators_hyphenated_names(self):
        """Test prefix numeric multimatch (N-name) including hyphenated names."""
        self.assertEqual(cmdparser.try_num_differentiators("1-ball"), (1, "ball"))
        self.assertEqual(cmdparser.try_num_differentiators("23-ball"), (23, "ball"))
        self.assertEqual(cmdparser.try_num_differentiators("1-t-shirt"), (1, "t-shirt"))
        self.assertEqual(cmdparser.try_num_differentiators("2-t-shirt"), (2, "t-shirt"))
        self.assertEqual(
            cmdparser.try_num_differentiators("3-some-long-name"), (3, "some-long-name")
        )
        self.assertEqual(cmdparser.try_num_differentiators("t-shirt"), (None, None))
        self.assertEqual(cmdparser.try_num_differentiators("ball"), (None, None))
        self.assertEqual(cmdparser.try_num_differentiators("1-t-shirt arg"), (1, "t-shirt arg"))
        self.assertEqual(
            cmdparser.try_num_differentiators("2-ball some args"), (2, "ball some args")
        )

    def test_ordinal_differentiators(self):
        self.assertEqual(cmdparser.try_multimatch_differentiators("first look"), (0, "look"))
        self.assertEqual(cmdparser.try_multimatch_differentiators("last look"), ("last", "look"))

    @override_settings(SEARCH_MULTIMATCH_REGEX=r"(?P<number>[0-9]+)-(?P<name>.*)")
    def test_cmdparser(self):
        a_cmdset = _CmdSetTest()
        bcmd = [cmd for cmd in a_cmdset.commands if cmd.key == "test1"][0]

        self.assertEqual(
            cmdparser.cmdparser("test1hello", a_cmdset, None),
            [("test1", "hello", bcmd, 5, 0.5, "test1")],
        )


class TestCmdSetNesting(BaseEvenniaTest):
    """
    Test 'nesting' of cmdsets by adding
    """

    def test_nest(self):
        class CmdA(Command):
            key = "a"

            def func(self):
                self.msg(str(self.obj))

        class CmdSetA(CmdSet):
            def at_cmdset_creation(self):
                self.add(CmdA)

        class CmdSetB(CmdSet):
            def at_cmdset_creation(self):
                self.add(CmdSetA)

        cmd = self.char1.cmdset.cmdset_stack[-1].commands[0]
        self.assertEqual(cmd.obj, self.char1)


class TestCmdSet(BaseEvenniaTest):
    """
    General tests for cmdsets
    """

    def test_cmdset_remove_by_key(self):
        test_cmd_set = _CmdSetTest()
        test_cmd_set.remove("another command")

        self.assertNotIn(_CmdTest2, test_cmd_set.commands)

    def test_cmdset_gets_by_key(self):
        test_cmd_set = _CmdSetTest()
        result = test_cmd_set.get("another command")

        self.assertIsInstance(result, _CmdTest2)

    def test_cmdset_remove_returns_bool(self):
        test_cmd_set = _CmdSetTest()
        self.assertTrue(test_cmd_set.remove("another command"))
        self.assertFalse(test_cmd_set.remove("another command"))
        self.assertFalse(test_cmd_set.remove("never existed"))

    def test_cmdset_remove_strict_raises(self):
        test_cmd_set = _CmdSetTest()
        with self.assertRaises(KeyError):
            test_cmd_set.remove("never existed", strict=True)
        # strict on a present key still works and returns True
        self.assertTrue(test_cmd_set.remove("another command", strict=True))

    def test_cmdset_remove_missing_syscmd_string(self):
        # Previously raised AttributeError on cmd.key when looking up a
        # missing system command by string.
        test_cmd_set = _CmdSetTest()
        self.assertFalse(test_cmd_set.remove("__missing_sys"))

    def test_cmdset_remove_syscmd_by_key(self):
        class _SysCmd(Command):
            key = "__sys"

        cmdset = CmdSet()
        cmdset.add(_SysCmd())
        self.assertEqual(len(cmdset.system_commands), 1)
        self.assertTrue(cmdset.remove("__sys"))
        self.assertEqual(cmdset.system_commands, [])

    def test_cmdset_has(self):
        test_cmd_set = _CmdSetTest()
        self.assertTrue(test_cmd_set.has("another command"))
        self.assertFalse(test_cmd_set.has("nope"))
        # by instance
        cmd = test_cmd_set.get("another command")
        self.assertTrue(test_cmd_set.has(cmd))

    def test_cmdset_replace(self):
        class _CmdReplacement(AccessableCommand):
            key = "another command"
            arg_regex = None

        test_cmd_set = _CmdSetTest()
        original = test_cmd_set.get("another command")
        self.assertIsInstance(original, _CmdTest2)

        found = test_cmd_set.replace("another command", _CmdReplacement())
        self.assertTrue(found)
        self.assertIsInstance(test_cmd_set.get("another command"), _CmdReplacement)

        # replacing something that wasn't there still adds, returns False
        class _CmdNew(AccessableCommand):
            key = "brand new"
            arg_regex = None

        found = test_cmd_set.replace("brand new", _CmdNew())
        self.assertFalse(found)
        self.assertTrue(test_cmd_set.has("brand new"))

    def test_cmdset_add_allow_duplicates(self):
        class _CmdDuplicateA(Command):
            key = "duplicate"

        class _CmdDuplicateB(Command):
            key = "duplicate"

        cmdset = CmdSet()
        cmdset.add(_CmdDuplicateA, allow_duplicates=True)
        cmdset.add(_CmdDuplicateB, allow_duplicates=True)

        duplicate_cmds = [cmd for cmd in cmdset.commands if cmd.key == "duplicate"]
        self.assertEqual(len(duplicate_cmds), 2)
        self.assertEqual(
            {cmd.__class__ for cmd in duplicate_cmds}, {_CmdDuplicateA, _CmdDuplicateB}
        )


class _CmdG(Command):
    key = "smile"
    aliases = ["smile at", "grin", "grin at"]


class _CmdSetG(CmdSet):
    def at_cmdset_creation(self):
        self.add(_CmdG())


class TestIssue3090(BaseEvenniaTest):
    """
    Command aliases should be prioritized longest-match to shortest-match.
    https://github.com/evennia/evennia/issues/3090

    """

    def test_long_aliases(self):
        cmdset_g = _CmdSetG()

        # print(cmdset_g.commands[0]._keyaliases)

        result = cmdparser.cmdparser("smile at", cmdset_g, None)[0]
        self.assertEqual(result[0], "smile at")
        self.assertEqual(result[1], "")
        self.assertEqual(result[2].__class__, _CmdG)
        self.assertEqual(result[3], 8)
        self.assertEqual(result[4], 1.0)
        self.assertEqual(result[5], "smile at")


class _TestCmd1(Command):
    key = "testcmd"
    locks = "usecmd:false()"

    def func():
        pass


class TestIssue3643(BaseEvenniaTest):
    """
    Commands with a 'cmd:' anywhere in its string, even `funccmd:` is assumed to
    be a cmd: type lock, meaning it will not auto-insert `cmd:all()` into the
    lockstring as intended.

    """

    def test_issue_3643(self):
        cmd = _TestCmd1()
        self.assertEqual(cmd.locks, "cmd:all();usecmd:false()")


class _CmdCrash(Command):
    key = "connect"

    def func(self):
        raise RuntimeError("forced failure")


class TestIssue2627(TwistedTestCase, BaseEvenniaTest):
    """
    Prevent logging plaintext credentials in command-error reporting.
    https://github.com/evennia/evennia/issues/2627
    """

    def setUp(self):
        self.patch(sys.modules["evennia.server.sessionhandler"], "delay", _mockdelay)
        super().setUp()

    @patch.object(cmdhandler.logger, "log_err")
    def test_cmdhandler_masks_sensitive_input_in_error_log(self, mock_log_err):
        d = ensureDeferred(
            cmdhandler.cmdhandler(
                self.session, " johnny password123", cmdobj=_CmdCrash(), cmdobj_key="connect"
            )
        )

        def _callback(_):
            logged = [call.args[0] for call in mock_log_err.call_args_list if call.args]
            self.assertIn("User input was: 'connect johnny ***********'.", logged)
            self.assertNotIn(
                "User input was: 'connect johnny password123'.",
                logged,
            )

        d.addCallback(_callback)
        return d


class TestCmdSetMergeObjBindings(TestCase):
    """Test that cmdset merges preserve correct cmd.obj bindings."""

    def test_merge_preserves_obj_from_different_cmdsets(self):
        """Commands from different objects retain their obj after merge."""
        from unittest.mock import Mock

        obj1 = Mock(name="Sword")
        obj2 = Mock(name="Shield")

        cmdset1 = CmdSet(obj1)
        cmdset1.key = "SwordCmds"
        cmd_slash = _CmdA("sword")
        cmd_slash.obj = obj1
        cmdset1.add(cmd_slash)

        cmdset2 = CmdSet(obj2)
        cmdset2.key = "ShieldCmds"
        cmd_block = _CmdB("shield")
        cmd_block.obj = obj2
        cmdset2.add(cmd_block)

        merged = cmdset1 + cmdset2
        cmds = {cmd.key: cmd for cmd in merged.commands}

        self.assertIs(cmds["a"].obj, obj1)
        self.assertIs(cmds["b"].obj, obj2)

    def test_same_key_different_obj_resolved_by_priority(self):
        """When two cmdsets share a command key, priority determines which obj wins."""
        from unittest.mock import Mock

        obj_low = Mock(name="LowPrio")
        obj_high = Mock(name="HighPrio")

        cmdset_low = CmdSet(obj_low)
        cmdset_low.key = "LowSet"
        cmdset_low.priority = 0
        cmd_low = _CmdA("low")
        cmd_low.obj = obj_low
        cmdset_low.add(cmd_low)

        cmdset_high = CmdSet(obj_high)
        cmdset_high.key = "HighSet"
        cmdset_high.priority = 1
        cmd_high = _CmdA("high")
        cmd_high.obj = obj_high
        cmdset_high.add(cmd_high)

        merged = cmdset_low + cmdset_high
        result_cmd = [cmd for cmd in merged.commands if cmd.key == "a"][0]

        self.assertIs(result_cmd.obj, obj_high)

    def test_merge_after_add_reflects_new_command(self):
        """Adding a command to a cmdset and re-merging includes it with correct obj."""
        from unittest.mock import Mock

        obj1 = Mock(name="Obj1")
        obj2 = Mock(name="Obj2")

        cmdset1 = CmdSet(obj1)
        cmdset1.key = "Set1"
        cmd_a = _CmdA("set1")
        cmd_a.obj = obj1
        cmdset1.add(cmd_a)

        cmdset2 = CmdSet(obj2)
        cmdset2.key = "Set2"
        cmd_b = _CmdB("set2")
        cmd_b.obj = obj2
        cmdset2.add(cmd_b)

        merged1 = cmdset1 + cmdset2
        self.assertEqual(len(merged1.commands), 2)

        # add a new command and re-merge
        cmd_c = _CmdC("set1")
        cmd_c.obj = obj1
        cmdset1.add(cmd_c)

        merged2 = cmdset1 + cmdset2
        self.assertEqual(len(merged2.commands), 3)
        cmds = {cmd.key: cmd for cmd in merged2.commands}
        self.assertIn("c", cmds)
        self.assertIs(cmds["c"].obj, obj1)

    def test_merge_after_remove_excludes_command(self):
        """Removing a command from a cmdset and re-merging excludes it."""
        from unittest.mock import Mock

        obj1 = Mock(name="Obj1")
        obj2 = Mock(name="Obj2")

        cmdset1 = CmdSet(obj1)
        cmdset1.key = "Set1"
        cmd_a = _CmdA("set1")
        cmd_a.obj = obj1
        cmd_b = _CmdB("set1")
        cmd_b.obj = obj1
        cmdset1.add(cmd_a)
        cmdset1.add(cmd_b)

        cmdset2 = CmdSet(obj2)
        cmdset2.key = "Set2"
        cmd_c = _CmdC("set2")
        cmd_c.obj = obj2
        cmdset2.add(cmd_c)

        merged1 = cmdset1 + cmdset2
        self.assertEqual(len(merged1.commands), 3)

        # remove a command and re-merge
        cmdset1.remove(cmd_b)

        merged2 = cmdset1 + cmdset2
        self.assertEqual(len(merged2.commands), 2)
        keys = {cmd.key for cmd in merged2.commands}
        self.assertNotIn("b", keys)


class TestCmdAccessCache(BaseEvenniaTest):
    """Tests for evennia.commands.cmd_access_cache."""

    def setUp(self):
        super().setUp()
        self.char1.ndb._cmd_access_cache = {}
        self.char1.ndb._cmd_access_cache_gen = 0

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_cache_hit_avoids_second_access_call(self):
        from evennia.commands.cmd_access_cache import cached_cmd_access

        cmd = _CmdA("test")
        with patch.object(cmd, "access", wraps=cmd.access) as mock_access:
            mock_access.return_value = True
            self.assertTrue(cached_cmd_access(cmd, self.char1))
            self.assertTrue(cached_cmd_access(cmd, self.char1))
            self.assertEqual(mock_access.call_count, 1)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_invalidate_bumps_generation(self):
        from evennia.commands.cmd_access_cache import cached_cmd_access, invalidate_cmd_access_cache

        cmd = _CmdA("test")
        with patch.object(cmd, "access", return_value=True) as mock_access:
            cached_cmd_access(cmd, self.char1)
            invalidate_cmd_access_cache(self.char1)
            cached_cmd_access(cmd, self.char1)
            self.assertEqual(mock_access.call_count, 2)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_cmdset_add_invalidates_caller(self):
        from evennia.commands import cmd_access_cache
        from evennia.commands.cmdset import CmdSet

        class _OneCmdSet(CmdSet):
            def at_cmdset_creation(self):
                self.add(_CmdA(self))

        cmd = _CmdA("x")
        with patch.object(
            cmd_access_cache, "cached_cmd_access", wraps=cmd_access_cache.cached_cmd_access
        ) as wrapped:
            wrapped(cmd, self.char1)
            self.assertEqual(wrapped.call_count, 1)
            self.char1.cmdset.add(_OneCmdSet)
            wrapped(cmd, self.char1)
            self.assertEqual(wrapped.call_count, 2)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=False)
    def test_disabled_uses_access_directly(self):
        from evennia.commands.cmd_access_cache import cached_cmd_access

        cmd = _CmdA("test")
        with patch.object(cmd, "access", return_value=True) as mock_access:
            cached_cmd_access(cmd, self.char1)
            cached_cmd_access(cmd, self.char1)
            self.assertEqual(mock_access.call_count, 2)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_cmdparser_with_cache_enabled(self):
        from evennia.commands.cmdset import CmdSet

        class _SayCmd(Command):
            key = "saytest"
            locks = "cmd:all()"

        class _SayCmdSet(CmdSet):
            def at_cmdset_creation(self):
                self.add(_SayCmd())

        cmdset = _SayCmdSet()
        matches = cmdparser.cmdparser("saytest hello", cmdset, self.char1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], "saytest")


class TestCmdAccessCacheBypassOnAccessOverride(BaseEvenniaTest):
    """Commands that override .access() bypass the cache (F-6 auto-skip)."""

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_overriding_access_bypasses_cache(self):
        from evennia.commands.cmd_access_cache import cached_cmd_access

        class _CustomAccessCmd(_CmdA):
            def access(self, srcobj, access_type="cmd", default=False, session=None):
                return super().access(srcobj, access_type, default, session)

        cmd = _CustomAccessCmd("custom")
        with patch.object(cmd, "access", wraps=cmd.access) as mock_access:
            mock_access.return_value = True
            cached_cmd_access(cmd, self.char1)
            cached_cmd_access(cmd, self.char1)
        self.assertEqual(mock_access.call_count, 2)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_inherited_override_bypasses_cache(self):
        """Override two classes up still bypasses (the is-check sees the inherited fn)."""
        from evennia.commands.cmd_access_cache import cached_cmd_access

        class _OverridingBase(_CmdA):
            def access(self, srcobj, access_type="cmd", default=False, session=None):
                return True

        class _InheritsOverride(_OverridingBase):
            pass

        cmd = _InheritsOverride("inherit")
        with patch.object(cmd, "access", wraps=cmd.access) as mock_access:
            mock_access.return_value = True
            cached_cmd_access(cmd, self.char1)
            cached_cmd_access(cmd, self.char1)
        self.assertEqual(mock_access.call_count, 2)

    @override_settings(COMMAND_ACCESS_CACHE_ENABLED=True)
    def test_base_access_still_cached(self):
        """Stock Command.access subclasses (no override) still get cached."""
        from evennia.commands.cmd_access_cache import cached_cmd_access

        cmd = _CmdA("base")
        with patch.object(cmd, "access", wraps=cmd.access) as mock_access:
            mock_access.return_value = True
            cached_cmd_access(cmd, self.char1)
            cached_cmd_access(cmd, self.char1)
        self.assertEqual(mock_access.call_count, 1)


class TestInvalidateCallerAccess(BaseEvenniaTest):
    """Tests for cmd_access_cache.invalidate_caller_access fan-out."""

    def test_calls_both_inner_invalidations_for_single_target(self):
        from evennia.commands import cmd_access_cache
        from evennia.locks import lockhandler

        with patch.object(cmd_access_cache, "invalidate_cmd_access_cache") as cmd_inv:
            with patch.object(lockhandler, "invalidate_lock_cache") as lock_inv:
                cmd_access_cache.invalidate_caller_access(self.char1)
        cmd_inv.assert_called_once_with(self.char1)
        lock_inv.assert_called_once_with(self.char1)

    def test_multi_target_invalidates_each(self):
        from evennia.commands import cmd_access_cache
        from evennia.locks import lockhandler

        with patch.object(cmd_access_cache, "invalidate_cmd_access_cache") as cmd_inv:
            with patch.object(lockhandler, "invalidate_lock_cache") as lock_inv:
                cmd_access_cache.invalidate_caller_access(self.char1, self.account)
        self.assertEqual(cmd_inv.call_count, 2)
        self.assertEqual(lock_inv.call_count, 2)
        cmd_inv.assert_any_call(self.char1)
        cmd_inv.assert_any_call(self.account)
        lock_inv.assert_any_call(self.char1)
        lock_inv.assert_any_call(self.account)

    def test_none_targets_skipped(self):
        from evennia.commands import cmd_access_cache
        from evennia.locks import lockhandler

        with patch.object(cmd_access_cache, "invalidate_cmd_access_cache") as cmd_inv:
            with patch.object(lockhandler, "invalidate_lock_cache") as lock_inv:
                cmd_access_cache.invalidate_caller_access(self.char1, None)
        cmd_inv.assert_called_once_with(self.char1)
        lock_inv.assert_called_once_with(self.char1)

    def test_zero_targets_is_noop(self):
        from evennia.commands import cmd_access_cache
        from evennia.locks import lockhandler

        with patch.object(cmd_access_cache, "invalidate_cmd_access_cache") as cmd_inv:
            with patch.object(lockhandler, "invalidate_lock_cache") as lock_inv:
                cmd_access_cache.invalidate_caller_access()
        cmd_inv.assert_not_called()
        lock_inv.assert_not_called()


# ----------------------------------------------------------------------------
# Phase 2 step 1: at_pre_cmd → at_pre_parse rename + post-parse at_pre_cmd
# ----------------------------------------------------------------------------


class TestAtPreCmdRename(BaseEvenniaTest):
    """Hook dispatch order and the __init_subclass__ guard."""

    def test_dispatch_order_calls_pre_parse_then_parse_then_pre_cmd_then_func(self):
        events = []

        class _CmdOrder(Command):
            key = "order"
            locks = "cmd:all()"

            def at_pre_parse(self):
                events.append("at_pre_parse")

            def parse(self):
                events.append("parse")

            def func(self):
                events.append("func")

            def at_post_cmd(self):
                events.append("at_post_cmd")

        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, "", cmdobj=_CmdOrder(), cmdobj_key="order")
        )

        def _check(_):
            # at_pre_cmd is engine-only (no-op) during the deprecation
            # window, so it doesn't show up in events but still runs in
            # dispatch. Order of the hooks the subclass CAN observe:
            self.assertEqual(events, ["at_pre_parse", "parse", "func", "at_post_cmd"])

        d.addCallback(_check)
        return d

    def test_pre_parse_truthy_return_aborts_before_parse(self):
        events = []

        class _CmdAbortPre(Command):
            key = "abortpre"
            locks = "cmd:all()"

            def at_pre_parse(self):
                events.append("at_pre_parse")
                return True

            def parse(self):
                events.append("parse")

            def func(self):
                events.append("func")

        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, "", cmdobj=_CmdAbortPre(), cmdobj_key="abortpre")
        )

        def _check(_):
            self.assertEqual(events, ["at_pre_parse"])

        d.addCallback(_check)
        return d

    def test_at_pre_cmd_override_runs_after_parse_before_func(self):
        # As of 6.0.0+underspire.4 the __init_subclass__ guard is gone;
        # at_pre_cmd is freely subclass-able and fires post-parse.
        events = []

        class _CmdPostParseHook(Command):
            key = "postparsehook"
            locks = "cmd:all()"

            def at_pre_parse(self):
                events.append("at_pre_parse")

            def parse(self):
                events.append("parse")

            def at_pre_cmd(self):
                events.append("at_pre_cmd")

            def func(self):
                events.append("func")

            def at_post_cmd(self):
                events.append("at_post_cmd")

        d = ensureDeferred(
            cmdhandler.cmdhandler(
                self.session, "", cmdobj=_CmdPostParseHook(), cmdobj_key="postparsehook"
            )
        )

        def _check(_):
            self.assertEqual(
                events,
                ["at_pre_parse", "parse", "at_pre_cmd", "func", "at_post_cmd"],
            )

        d.addCallback(_check)
        return d

    def test_at_pre_cmd_truthy_return_aborts_after_parse_before_func(self):
        events = []

        class _CmdAbortPost(Command):
            key = "abortpost"
            locks = "cmd:all()"

            def at_pre_parse(self):
                events.append("at_pre_parse")

            def parse(self):
                events.append("parse")

            def at_pre_cmd(self):
                events.append("at_pre_cmd")
                return True

            def func(self):
                events.append("func")

            def at_post_cmd(self):
                events.append("at_post_cmd")

        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, "", cmdobj=_CmdAbortPost(), cmdobj_key="abortpost")
        )

        def _check(_):
            # parse ran (post-parse hook only fires after) but func and
            # at_post_cmd are skipped on truthy abort.
            self.assertEqual(events, ["at_pre_parse", "parse", "at_pre_cmd"])

        d.addCallback(_check)
        return d


# ----------------------------------------------------------------------------
# Phase 2 step 6: ftfy normalisation at cmdhandler entry
# ----------------------------------------------------------------------------


class TestFtfyNormalization(BaseEvenniaTest):
    """`ftfy.fix_text` runs on `raw_string` at cmdhandler entry.

    Mojibake-bearing input arrives at `cmd.raw_string` already repaired
    when `INPUT_FTFY_NORMALIZE` is True; when False, the raw value
    passes through untouched.
    """

    # Classic mojibake: "café" round-tripped through latin-1 -> utf-8.
    MOJIBAKE_ARG = " cafÃ©"
    FIXED_ARG = " café"

    def _capture_cmd(self):
        captured = {}

        class _CmdCapture(Command):
            key = "key"
            locks = "cmd:all()"

            def func(self):
                captured["raw_string"] = self.raw_string

        return _CmdCapture(), captured

    @override_settings(INPUT_FTFY_NORMALIZE=True)
    def test_mojibake_normalized_on_raw_string(self):
        cmd, captured = self._capture_cmd()
        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, self.MOJIBAKE_ARG, cmdobj=cmd, cmdobj_key="key")
        )

        def _check(_):
            self.assertEqual(captured["raw_string"], "key" + self.FIXED_ARG)

        d.addCallback(_check)
        return d

    @override_settings(INPUT_FTFY_NORMALIZE=False)
    def test_setting_off_passes_through(self):
        cmd, captured = self._capture_cmd()
        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, self.MOJIBAKE_ARG, cmdobj=cmd, cmdobj_key="key")
        )

        def _check(_):
            self.assertEqual(captured["raw_string"], "key" + self.MOJIBAKE_ARG)

        d.addCallback(_check)
        return d


# ----------------------------------------------------------------------------
# Tests for evennia.commands.signals (cmdhandler pre/post/error signals)
# ----------------------------------------------------------------------------


from evennia.commands.signals import on_command_error as _on_command_error
from evennia.commands.signals import on_command_post as _on_command_post
from evennia.commands.signals import on_command_pre as _on_command_pre


class _CmdSignalsOk(Command):
    key = "ok"
    locks = "cmd:all()"

    def func(self):
        pass


class _SignalRecorder:
    """Subscribes to the cmdhandler signals and records call order + kwargs."""

    def __init__(self):
        self.events = []
        _on_command_pre.connect(self._on_pre, weak=False, dispatch_uid=id(self))
        _on_command_post.connect(self._on_post, weak=False, dispatch_uid=id(self))
        _on_command_error.connect(self._on_error, weak=False, dispatch_uid=id(self))

    def disconnect(self):
        _on_command_pre.disconnect(self._on_pre, dispatch_uid=id(self))
        _on_command_post.disconnect(self._on_post, dispatch_uid=id(self))
        _on_command_error.disconnect(self._on_error, dispatch_uid=id(self))

    def _on_pre(self, sender, **kwargs):
        self.events.append(("pre", sender, kwargs))

    def _on_post(self, sender, **kwargs):
        self.events.append(("post", sender, kwargs))

    def _on_error(self, sender, **kwargs):
        self.events.append(("error", sender, kwargs))


class TestCommandSignals(TwistedTestCase, BaseEvenniaTest):
    """cmdhandler fires on_command_pre/post/error around dispatch.

    The signal *contract* (pre/post/error ordering, shared trace_id, the
    session/exc/traceback payload) is exercised against the live action-engine
    path in ``evennia.actions.tests.test_dispatch.TestSignals``; the empty-input
    ``cmdobj=`` injection used here only reaches the legacy ``_run_command``,
    which the action bridge now bypasses. What remains worth pinning here is
    send_robust receiver isolation.
    """

    def setUp(self):
        self.patch(sys.modules["evennia.server.sessionhandler"], "delay", _mockdelay)
        super().setUp()
        self.recorder = _SignalRecorder()

    def tearDown(self):
        self.recorder.disconnect()
        super().tearDown()

    def test_bad_receiver_does_not_break_dispatch(self):
        """send_robust isolates receiver failures."""

        def _bad(sender, **kwargs):
            raise ValueError("receiver exploded")

        _on_command_pre.connect(_bad, weak=False, dispatch_uid="bad-pre")
        self.addCleanup(_on_command_pre.disconnect, _bad, dispatch_uid="bad-pre")

        d = ensureDeferred(
            cmdhandler.cmdhandler(self.session, "", cmdobj=_CmdSignalsOk(), cmdobj_key="ok")
        )

        def _check(_):
            kinds = [ev[0] for ev in self.recorder.events]
            self.assertEqual(kinds, ["pre", "post"])

        d.addCallback(_check)
        return d


class TestSignalSessionResolution(TwistedTestCase, BaseEvenniaTest):
    """An explicit ``session=`` arg surfaces verbatim in signal payloads.

    The action engine passes the session it is handed straight into the
    signal kwargs (see ``actions.tests.test_dispatch.TestSignals``). The older
    cmdhandler-side ``_resolve_signal_session`` unwrap (real_session) and the
    "called_by IS the session, no explicit session arg" fallback only applied
    on the legacy ``_run_command`` path the action bridge now bypasses; whether
    the action path needs that same unwrap/fallback is a separate open question.
    """

    def setUp(self):
        self.patch(sys.modules["evennia.server.sessionhandler"], "delay", _mockdelay)
        super().setUp()
        self.recorder = _SignalRecorder()

    def tearDown(self):
        self.recorder.disconnect()
        super().tearDown()

    def test_proxy_without_real_session_attr_passes_through(self):
        # A proxy with no `real_session` attribute (synthetic session)
        # is returned as-is. Receivers can isinstance-check if needed.
        class _SyntheticSession:
            def __init__(self, providers):
                self._providers = providers

            def get_cmdset_providers(self):
                return self._providers

        synthetic = _SyntheticSession(self.session.get_cmdset_providers())
        d = ensureDeferred(
            cmdhandler.cmdhandler(
                self.session,
                "",
                cmdobj=_CmdSignalsOk(),
                cmdobj_key="ok",
                session=synthetic,
            )
        )

        def _check(_):
            post_kwargs = self.recorder.events[1][2]
            self.assertIs(post_kwargs["session"], synthetic)

        d.addCallback(_check)
        return d


class TestErrorReportedTraceId(TwistedTestCase, BaseEvenniaTest):
    """Phase 1: ErrorReported carries trace_id when raised inside a trace."""

    def test_trace_id_set_inside_trace(self):
        from evennia.utils.command_trace import begin_command_trace, end_command_trace

        try:
            tid = begin_command_trace(raw_string="x", cmd_key="x")
            err = cmdhandler.ErrorReported("x")
            self.assertEqual(err.trace_id, tid)
        finally:
            end_command_trace()

    def test_trace_id_none_outside_trace(self):
        err = cmdhandler.ErrorReported("x")
        self.assertIsNone(err.trace_id)


class TestSessionProxy(TwistedTestCase, BaseEvenniaTest):
    """Phase 1: cmdhandler accepts a duck-typed session-proxy."""

    def test_proxy_session_get_cmdset_providers_is_called(self):
        real_session = self.session
        real_providers = real_session.get_cmdset_providers()

        class _Proxy:
            called = False

            def get_cmdset_providers(self_inner):
                self_inner.__class__.called = True
                return dict(real_providers)

        proxy = _Proxy()
        providers, _list, _err_list, caller, _error_to = cmdhandler.generate_cmdset_providers(
            real_session, session=proxy
        )
        self.assertTrue(_Proxy.called)
        self.assertTrue(providers)


# ----------------------------------------------------------------------------
# Tests for evennia.commands.location_cmdset_cache
# (merged in from the formerly-orphaned evennia/commands/tests/
#  directory, which was shadowed by this tests.py module.)
# ----------------------------------------------------------------------------


from evennia.commands.location_cmdset_cache import (
    bump_cmdset_generation,
    clear_location_cmdset_cache,
    cmdset_generation,
    get_cached_location_cmdsets,
    make_cache_key,
    set_cached_location_cmdsets,
)


class TestLocationCmdsetCache(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        # The cache is a process-global OrderedDict. Without cleanup, sentinel
        # values written by test_cache_roundtrip leak into later tests
        # (TestBuilding.test_tunnel etc.) and trip cmdset.key attribute access.
        self.addCleanup(clear_location_cmdset_cache)

    def test_generation_bumps_on_cmdset_change(self):
        gen0 = cmdset_generation(self.char1)
        self.char1.cmdset.add("evennia.commands.default.cmdset_character.CharacterCmdSet")
        self.assertGreater(cmdset_generation(self.char1), gen0)
        if self.char1.location:
            self.assertGreaterEqual(cmdset_generation(self.char1.location), gen0)

    def test_cache_roundtrip(self):
        key = make_cache_key(self.char1, self.char1.location)
        sentinel = ["cmdset-list"]
        set_cached_location_cmdsets(key, sentinel)
        self.assertIs(get_cached_location_cmdsets(key), sentinel)

    def test_bump_cmdset_generation_docstring_describes_contract(self):
        # The hook is the public invalidation contract for downstream
        # caches built on the same generation counter. Make sure the
        # contract docstring stays attached.
        doc = bump_cmdset_generation.__doc__ or ""
        self.assertIn("invalidation contract", doc.lower())
        self.assertIn("intended consumers", doc.lower())


# ----------------------------------------------------------------------------
# Tests for AccountCommand caller normalisation (Phase 2 step 2,
# shipped in 6.0.0+underspire.3).
# ----------------------------------------------------------------------------


from evennia.commands.command import AccountCommand as _AccountCommand


class _CmdAcctMarker(_AccountCommand):
    key = "acctmarker"
    locks = "cmd:all()"
    # retain_instance so the cmd we hand to cmdhandler is the one normalised
    # in-place (the default copy path would rebind to a separate object).
    retain_instance = True

    def func(self):
        pass


class TestAccountCommandNormalization(TwistedTestCase, BaseEvenniaTest):
    """AccountCommand caller/account/character normalisation.

    The runtime normalisation (session callertype → effective character/account)
    is exercised against the action engine in
    ``actions.tests.test_dispatch.TestFromCaller``; the legacy ``cmdobj=`` +
    ``_testing=True`` injection that drove ``_normalize_account_command_caller``
    only reaches ``_run_command``, which the action bridge bypasses. What stays
    here is the static class-flag and the MuxCommand-style ``parse()`` contract.
    """

    def test_account_command_caller_flag_default_false_on_base_command(self):
        self.assertFalse(Command.account_command_caller)
        self.assertTrue(_AccountCommand.account_command_caller)
        self.assertTrue(_CmdAcctMarker.account_command_caller)

    def test_command_parse_handles_mux_switches_and_lhs_rhs(self):
        # Command.parse now does MuxCommand-style parsing by default
        # (parse_mux_syntax=True, since +underspire.5).
        cmd = Command()
        cmd.cmdstring = "test"
        cmd.args = "/foo/bar a, b = c, d"
        cmd.parse()
        self.assertEqual(cmd.switches, ["foo", "bar"])
        self.assertEqual(cmd.args, "a, b = c, d")
        self.assertEqual(cmd.lhs, "a, b")
        self.assertEqual(cmd.rhs, "c, d")
        self.assertEqual(cmd.lhslist, ["a", "b"])
        self.assertEqual(cmd.rhslist, ["c", "d"])

    def test_command_parse_is_noop_when_parse_mux_syntax_false(self):
        # Subclasses can opt out with `parse_mux_syntax = False` and
        # still safely call super().parse() (back to legacy no-op shape).
        class _CmdRawArgs(Command):
            key = "rawargs"
            parse_mux_syntax = False

        cmd = _CmdRawArgs()
        cmd.cmdstring = "rawargs"
        cmd.args = "/foo a = b"
        cmd.parse()
        # No switch parsing happened: args is untouched, no switches attr set.
        self.assertEqual(cmd.args, "/foo a = b")
        self.assertFalse(hasattr(cmd, "switches"))
        self.assertFalse(hasattr(cmd, "lhs"))

    def test_command_parse_lowercases_switches_by_default(self):
        # Default since +underspire.15: switches arrive lowercased on
        # self.switches so consumers can compare against lowercase
        # literals without each one re-implementing case folding.
        cmd = Command()
        cmd.cmdstring = "test"
        cmd.args = "/Del/Force a"
        cmd.parse()
        self.assertEqual(cmd.switches, ["del", "force"])

    def test_command_parse_switch_options_accepts_mixed_case_input(self):
        # Regression: switch_options is class-lowercased, so without
        # input-side lowercasing /Del against switch_options=["del"]
        # was silently rejected as an unused switch. The lowercase
        # default fixes that.
        class _CmdValidated(Command):
            key = "v"
            switch_options = ("del", "force")

        cmd = _CmdValidated()
        cmd.cmdstring = "v"
        cmd.args = "/Del a"
        # msg would be called with an "Extra switch" warning on the bug;
        # collect to assert it does NOT fire.
        warnings = []
        cmd.msg = lambda text=None, **_: warnings.append(text)
        cmd.parse()
        self.assertEqual(cmd.switches, ["del"])
        self.assertEqual(warnings, [])

    def test_command_parse_opt_out_preserves_switch_case(self):
        # parse_lowercase_switches = False keeps the historical
        # case-preserving shape for any subclass that genuinely wants
        # case-sensitive switch handling.
        class _CmdCaseSensitive(Command):
            key = "cs"
            parse_lowercase_switches = False

        cmd = _CmdCaseSensitive()
        cmd.cmdstring = "cs"
        cmd.args = "/Del/Force a"
        cmd.parse()
        self.assertEqual(cmd.switches, ["Del", "Force"])

    def test_cmd_access_cache_identity_differentiates_command_classes(self):
        # _cmd_identity keys on class module + name, so a Command and an
        # AccountCommand with the same key string do not collide in the
        # cmd_access_cache even though they share a key.
        from evennia.commands.cmd_access_cache import _cmd_identity

        class _CmdSharedKeyObj(Command):
            key = "shared"

        class _CmdSharedKeyAcct(_AccountCommand):
            key = "shared"

        self.assertNotEqual(
            _cmd_identity(_CmdSharedKeyObj()),
            _cmd_identity(_CmdSharedKeyAcct()),
        )


# ----------------------------------------------------------------------------
# Phase 3 step 4: token-boundary matching semantics
# ----------------------------------------------------------------------------


class TestTokenBoundaryMatch(TestCase):
    """`Command.match` is token-boundary and prefix-literal.

    `@open` and `open` are distinct keys, and a key only matches when the
    next character of the input is a boundary (whitespace, `/`, newline,
    or end-of-string). Prefix-strip was removed in +underspire.8.
    """

    def _make_cmd(self, key, aliases=None):
        class _Cmd(Command):
            pass

        _Cmd.key = key
        _Cmd.aliases = list(aliases or [])
        cmd = _Cmd()
        cmd._optimize()
        return cmd

    def test_exact_key_matches(self):
        cmd = self._make_cmd("look")
        self.assertEqual(cmd.match("look"), ("look", "look"))

    def test_key_with_trailing_space_matches(self):
        cmd = self._make_cmd("look")
        self.assertEqual(cmd.match("look here"), ("look", "look"))

    def test_key_without_boundary_does_not_match(self):
        # `looker` must NOT match `look` — the boundary requires
        # whitespace/EOI after the key.
        cmd = self._make_cmd("look")
        self.assertEqual(cmd.match("looker"), (None, None))

    def test_at_prefix_is_load_bearing(self):
        # `@open foo = bar` matches the @-keyed command...
        at_cmd = self._make_cmd("@open")
        self.assertEqual(at_cmd.match("@open foo = bar"), ("@open", "@open"))
        # ...but plain `open foo` does NOT match (no prefix-strip).
        self.assertEqual(at_cmd.match("open foo"), (None, None))

    def test_unprefixed_key_does_not_match_prefixed_input(self):
        # Conversely, an unprefixed key does not match `@key`.
        cmd = self._make_cmd("open")
        self.assertEqual(cmd.match("@open foo"), (None, None))
        self.assertEqual(cmd.match("open foo"), ("open", "open"))

    def test_alias_follows_same_rule(self):
        cmd = self._make_cmd("@ban", aliases=["@bans"])
        self.assertEqual(cmd.match("@bans alice"), ("@bans", "@bans"))
        self.assertEqual(cmd.match("bans alice"), (None, None))

    def test_no_noprefix_aliases_attribute(self):
        # _noprefix_aliases is gone since +underspire.8.
        cmd = self._make_cmd("@open")
        self.assertFalse(hasattr(cmd, "_noprefix_aliases"))


# ----------------------------------------------------------------------------
# Phase 2 follow-up: engine-owned permission cache invalidation + signal
# ----------------------------------------------------------------------------


class TestPermissionsChangedSignal(BaseEvenniaCommandTest):
    """Engine commands that mutate effective permissions invalidate the
    cmd_access cache for the affected entity and fire the
    ``permissions_changed`` signal exactly once. Replaces downstream
    monkey-patches around ``CmdPerm`` / ``CmdQuell``.
    """

    def setUp(self):
        super().setUp()
        from evennia.commands.signals import permissions_changed

        self._captured = []

        def _receiver(sender, **kwargs):
            self._captured.append((sender, kwargs))

        self._receiver = _receiver
        permissions_changed.connect(_receiver)
        self.addCleanup(permissions_changed.disconnect, _receiver)

    def _prime_cache(self, caller):
        # Put something in the cache so we can detect invalidation by its
        # absence. Don't go through cached_cmd_access — the cache there
        # only populates when COMMAND_ACCESS_CACHE_ENABLED is True.
        caller.ndb._cmd_access_cache = {("sentinel",): True}
        caller.ndb._cmd_access_cache_gen = 7

    def test_cmd_perm_add_invalidates_target_and_fires(self):
        from evennia.commands.default import admin

        self._prime_cache(self.obj1)
        self.call(
            admin.CmdPerm(),
            "Obj = Builder",
            "Permission 'Builder' given to Obj (the Object/Character).",
        )
        self.assertIsNone(getattr(self.obj1.ndb, "_cmd_access_cache", None))
        self.assertEqual(len(self._captured), 1)
        sender, kw = self._captured[0]
        self.assertIs(sender, admin.CmdPerm)
        self.assertIs(kw["target"], self.obj1)
        self.assertEqual(kw["added"], ("Builder",))
        self.assertEqual(kw["removed"], ())
        self.assertFalse(kw["account_mode"])

    def test_cmd_perm_del_fires_with_removed(self):
        from evennia.commands.default import admin

        self.obj1.permissions.add("Builder")
        self._prime_cache(self.obj1)
        self.call(
            admin.CmdPerm(),
            "/del Obj = Builder",
            "Permission Builder removed from Obj (if they existed).",
        )
        self.assertIsNone(getattr(self.obj1.ndb, "_cmd_access_cache", None))
        self.assertEqual(len(self._captured), 1)
        sender, kw = self._captured[0]
        self.assertIs(kw["target"], self.obj1)
        self.assertEqual(kw["added"], ())
        self.assertEqual(kw["removed"], ("Builder",))

    def test_cmd_perm_no_op_does_not_fire(self):
        # Setting a permission that already exists is a no-op — no
        # mutation, no invalidation, no signal. Case-insensitive check
        # so input casing doesn't matter.
        from evennia.commands.default import admin

        self.obj1.permissions.add("Builder")
        self._prime_cache(self.obj1)
        self.call(
            admin.CmdPerm(),
            "Obj = Builder",
            "Permission 'Builder' is already defined on Obj.",
        )
        # Cache untouched, no signal.
        self.assertIsNotNone(getattr(self.obj1.ndb, "_cmd_access_cache", None))
        self.assertEqual(self._captured, [])

    def test_cmd_quell_invalidates_account_and_puppet_and_fires(self):
        from evennia.commands.default import account as account_cmds

        # Make sure no _quell flag survives from a prior test.
        self.account.attributes.remove("_quell")
        self._prime_cache(self.account)
        self._prime_cache(self.char1)
        self.call(account_cmds.CmdQuell(), "", caller=self.account)
        self.assertIsNone(getattr(self.account.ndb, "_cmd_access_cache", None))
        # The session puppet (char1) should also have been invalidated.
        self.assertIsNone(getattr(self.char1.ndb, "_cmd_access_cache", None))
        self.assertEqual(len(self._captured), 1)
        sender, kw = self._captured[0]
        self.assertIs(sender, account_cmds.CmdQuell)
        self.assertIs(kw["target"], self.account)
        self.assertEqual(kw["added"], ())
        self.assertEqual(kw["removed"], ())
        self.assertTrue(kw["account_mode"])

    def test_cmd_unquell_fires_too(self):
        from evennia.commands.default import account as account_cmds

        # Pre-condition: account is quelled. Otherwise @unquell is a no-op
        # and shouldn't fire (matches the "no actual mutation" guard).
        self.account.attributes.add("_quell", True)
        self._prime_cache(self.account)
        cmd = account_cmds.CmdQuell()
        self.call(cmd, "", cmdstring="@unquell", caller=self.account)
        self.assertIsNone(getattr(self.account.ndb, "_cmd_access_cache", None))
        self.assertEqual(len(self._captured), 1)
        sender, kw = self._captured[0]
        self.assertIs(kw["target"], self.account)
        self.assertEqual(kw["added"], ())
        self.assertEqual(kw["removed"], ())
        self.assertTrue(kw["account_mode"])


# ---------------------------------------------------------------------------
# Phase 4: trie-backed parser + fuzzy suggestions
# ---------------------------------------------------------------------------

from unittest import mock as _trie_mock

from evennia.commands import cmdparser_trie


class _TrieCmdGoShard(Command):
    key = "go shard"
    aliases = ["shard"]
    locks = "cmd:all()"

    def func(self):
        pass


class _TrieCmdGo(Command):
    key = "go"
    locks = "cmd:all()"

    def func(self):
        pass


class _TrieCmdLook(Command):
    key = "look"
    locks = "cmd:all()"

    def func(self):
        pass


class _TrieCmdLookat(Command):
    key = "lookat"
    locks = "cmd:all()"

    def func(self):
        pass


class _TrieCmdZebra(Command):
    key = "zebraalpha"
    locks = "cmd:all()"

    def func(self):
        pass


class _TrieCmdZebraOverrideMatch(Command):
    key = "zebraalpha"
    locks = "cmd:all()"

    def match(self, search_string):
        return Command.match(self, search_string)

    def func(self):
        pass


class TestCommandTrie(TestCase):
    def test_insert_multiword_match(self):
        cs = CmdSet()
        cs.add(_TrieCmdGoShard())
        cs.add(_TrieCmdGo())
        t = cmdparser_trie.CommandTrie.from_cmdset(cs)
        self.assertIn("go", t.root)
        m = cmdparser_trie.trie_build_matches("go shard", cs)
        self.assertTrue(m)
        self.assertEqual(m[0][2].key, "go shard")

    def test_abbrev_unambiguous_rewrites_first_token_and_args(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        m = cmdparser_trie.trie_build_matches("l north", cs)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0][2].key, "look")
        self.assertEqual(m[0][1].lstrip(), "north")

    def test_abbrev_ambiguous_prefix_no_rewrite(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        cs.add(_TrieCmdLookat())
        trie = cmdparser_trie.CommandTrie.from_cmdset(cs)
        words, raw = cmdparser_trie._expand_first_token_abbrev(trie, ["loo"], "loo north")
        self.assertEqual(words[0], "loo")
        self.assertEqual(raw, "loo north")

    def test_abbrev_unambiguous_prefix_rewrites_words(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        trie = cmdparser_trie.CommandTrie.from_cmdset(cs)
        words, raw = cmdparser_trie._expand_first_token_abbrev(trie, ["l"], "l north")
        self.assertEqual(words[0], "look")
        self.assertEqual(raw, "look north")

    def test_trie_invalidates_on_add(self):
        """add() must invalidate the cached trie via the cheap-key check."""

        class _ExitOut(Command):
            key = "out"
            aliases = []
            is_exit = True
            locks = "cmd:all()"

            def func(self):
                pass

        class _ExitNorth(Command):
            key = "north"
            aliases = ["n"]
            is_exit = True
            locks = "cmd:all()"

            def func(self):
                pass

        cs = CmdSet()
        cs.add(_ExitOut())
        m0 = cmdparser_trie.trie_build_matches("n", cs)
        self.assertEqual(m0, [])
        cs.add(_ExitNorth())
        m1 = cmdparser_trie.trie_build_matches("n", cs)
        self.assertEqual(len(m1), 1)
        self.assertEqual(m1[0][2].key, "north")

    def test_abbrev_prefers_shortest_root_key_for_same_command(self):
        class _DualKey(Command):
            key = "out"
            aliases = ["o"]
            locks = "cmd:all()"

            def func(self):
                pass

        cs = CmdSet()
        cs.add(_DualKey())
        trie = cmdparser_trie.CommandTrie.from_cmdset(cs)
        words, raw = cmdparser_trie._expand_first_token_abbrev(trie, ["ou"], "ou")
        self.assertEqual(words[0], "out")
        self.assertEqual(raw, "out")

    def test_fastpath_skipped_for_exit_commands(self):
        class _ExitLike(Command):
            key = "out"
            aliases = ["o"]
            is_exit = True
            locks = "cmd:all()"

            def func(self):
                pass

        cs = CmdSet()
        cs.add(_ExitLike())
        calls = {"n": 0}
        orig = Command.match

        def wrapped(self, *a, **kw):
            calls["n"] += 1
            return orig(self, *a, **kw)

        with _trie_mock.patch.object(Command, "match", wrapped):
            cmdparser_trie.trie_build_matches("o", cs)
        self.assertGreaterEqual(calls["n"], 1)

    def test_fastpath_skips_command_match(self):
        cs = CmdSet()
        cs.add(_TrieCmdZebra())
        calls = {"n": 0}
        orig = Command.match

        def wrapped(self, *a, **kw):
            calls["n"] += 1
            return orig(self, *a, **kw)

        with _trie_mock.patch.object(Command, "match", wrapped):
            cmdparser_trie.trie_build_matches("zebraalpha tail", cs)
        self.assertEqual(calls["n"], 0)

    def test_fastpath_disabled_when_class_overrides_match(self):
        cs = CmdSet()
        cs.add(_TrieCmdZebraOverrideMatch())
        calls = {"n": 0}
        orig = Command.match

        def wrapped(self, *a, **kw):
            calls["n"] += 1
            return orig(self, *a, **kw)

        with _trie_mock.patch.object(Command, "match", wrapped):
            cmdparser_trie.trie_build_matches("zebraalpha tail", cs)
        self.assertGreaterEqual(calls["n"], 1)

    def test_fastpath_disabled_with_arg_regex(self):
        import re as _re

        class _Rx(Command):
            key = "zebraalpha"
            arg_regex = _re.compile(r"^@")
            locks = "cmd:all()"

            def func(self):
                pass

        cs = CmdSet()
        cs.add(_Rx())
        calls = {"n": 0}
        orig = Command.match

        def wrapped(self, *a, **kw):
            calls["n"] += 1
            return orig(self, *a, **kw)

        with _trie_mock.patch.object(Command, "match", wrapped):
            cmdparser_trie.trie_build_matches("zebraalpha tail", cs)
        self.assertGreaterEqual(calls["n"], 1)

    def test_pose_passthrough_yields_no_match(self):
        """`.pose smiles` must produce zero trie matches against engine-style keys.

        Newmoo and similar downstreams rely on a custom CMD_NOMATCH that
        interprets leading-punctuation input as pose/emote. The trie parser
        must not intercept ``.pose`` for any of its shortcuts (abbrev,
        fastpath) when no command key starts with ``.``.
        """
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        cs.add(_TrieCmdGo())
        m = cmdparser_trie.trie_build_matches(".pose smiles", cs)
        self.assertEqual(m, [])


class TestFuzzyCommandSuggestions(TestCase):
    def test_suggests_close_typo(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        out = cmdparser_trie.fuzzy_command_suggestions("loo", cs)
        self.assertIn("look", out)

    def test_skips_far_misses(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        out = cmdparser_trie.fuzzy_command_suggestions("xyzzyq", cs)
        self.assertEqual(out, [])

    def test_respects_limit(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        cs.add(_TrieCmdLookat())
        out = cmdparser_trie.fuzzy_command_suggestions("look", cs, limit=1)
        self.assertEqual(len(out), 1)

    def test_first_token_only(self):
        cs = CmdSet()
        cs.add(_TrieCmdLook())
        # Suggestions key off the first token; trailing junk does not pollute.
        out = cmdparser_trie.fuzzy_command_suggestions("loo blah blah", cs)
        self.assertIn("look", out)


# Engine-level pose / punctuation routing fixture. ``_POSE_RECORDER`` is module-
# level so per-test setUp can reset it; the recorder state stands in for a game's
# pose / NoMatchRules providers, letting the test assert how the real production
# parser routes leading-punctuation input through the engine bridge.
from evennia.actions.actor import Actor as _RPActor
from evennia.actions.default.roleplay import Pose as _RPPose
from evennia.actions.dispatch import try_action_dispatch as _rp_try_dispatch
from evennia.actions.engine import engine as _rp_engine
from evennia.actions.parser import NoMatchAction as _RPNoMatch
from evennia.actions.parser import parser as _rp_parser
from evennia.actions.result import PASS as _RP_PASS
from evennia.actions.rule import rule as _rp_rule
from evennia.actions.state import StateProvider as _RPStateProvider
from evennia.actions.state import enter_state as _rp_enter_state

_POSE_RECORDER = {"raw": None, "routed": None, "text": None}


class _PunctRoutingRecorderState(_RPStateProvider):
    """Stand-in for a game's pose / ``NoMatchRules`` providers: high-priority
    ``before`` rules on :class:`Pose` and :class:`NoMatchAction` record the action
    the parser produced and the verbatim text/raw it carried, without disturbing
    dispatch."""

    @_rp_rule(_RPPose, phase="before", priority=9999)
    def on_pose(self, action, actor):
        _POSE_RECORDER.update(routed="pose", text=action.text, raw=action._raw_string)
        return _RP_PASS

    @_rp_rule(_RPNoMatch, phase="before", priority=9999)
    def on_nomatch(self, action, actor):
        _POSE_RECORDER.update(routed="nomatch", text=action.raw_string, raw=action._raw_string)
        return _RP_PASS


class _PunctRoutingChar:
    """Minimal effective object for an engine actor: holds states, sends msgs, and
    resolves every search to nothing (so an unknown leading-punct verb no-matches)."""

    def __init__(self):
        self.key = "Poser"
        self.location = None
        self.account = None
        self.ndb = type("_ndb", (), {})()
        self.messages = []

    def msg(self, text=None, **kwargs):
        self.messages.append(text)

    def search(self, *args, **kwargs):
        return None


class TestPosePassthroughIntegration(TwistedTestCase):
    """Leading-punctuation input routes through the engine verbatim.

    The trie parser must not intercept ``.pose smiles`` / ``,grins`` via any of
    its shortcuts (abbrev, fastpath, fuzzy hint). The engine registers ``.`` and
    ``,`` as :class:`Pose` symbol verbs, so those route to the native Pose action
    carrying the text intact (this is why a downstream game can drop its legacy
    ``,``-overloaded ``CmdNoMatch``). An unregistered leading-punct verb (``;``)
    reaches a game ``NoMatchRules`` provider with ``raw_string`` verbatim.
    """

    def setUp(self):
        _POSE_RECORDER.update(raw=None, routed=None, text=None)
        self.char = _PunctRoutingChar()
        self.actor = _RPActor(character=self.char)
        _rp_enter_state(self.char, _PunctRoutingRecorderState())

    def _route(self, raw):
        out = {}
        # try_action_dispatch is `async def` now; drive the (synchronous)
        # coroutine to an already-fired Deferred.
        d = ensureDeferred(
            _rp_try_dispatch(self.char, raw, actor=self.actor, engine=_rp_engine, parser=_rp_parser)
        )
        d.addCallbacks(lambda r: out.__setitem__("ok", r), lambda f: out.__setitem__("fail", f))
        if "fail" in out:
            out["fail"].raiseException()
        return _POSE_RECORDER

    def test_dot_prefix_routes_to_pose_verbatim(self):
        rec = self._route(".pose smiles")
        self.assertEqual(rec["routed"], "pose")
        self.assertEqual(rec["text"], "pose smiles")
        self.assertEqual(rec["raw"], ".pose smiles")

    def test_comma_prefix_routes_to_pose_verbatim(self):
        rec = self._route(",grins")
        self.assertEqual(rec["routed"], "pose")
        self.assertEqual(rec["text"], ",grins")
        self.assertEqual(rec["raw"], ",grins")

    def test_unregistered_punct_reaches_nomatch_verbatim(self):
        rec = self._route(";nods")
        self.assertEqual(rec["routed"], "nomatch")
        self.assertEqual(rec["raw"], ";nods")


class TestCmdsetMergeWarmup(BaseEvenniaTest):
    """Cmdset merge cache warmup wires the same merge machinery a real
    command would, eagerly, so the first typed command after login or reload
    doesn't pay the cold-merge latency."""

    def test_schedule_for_character_no_sessions_is_noop(self):
        from evennia.commands import cmdset_merge_warmup

        with (
            patch.object(self.char1.sessions, "count", return_value=0),
            patch.object(cmdset_merge_warmup, "delay") as delay_mock,
        ):
            cmdset_merge_warmup.schedule_cmdset_merge_warmup_for_character(self.char1)
        delay_mock.assert_not_called()

    def test_schedule_for_character_with_sessions_defers(self):
        from evennia.commands import cmdset_merge_warmup

        with (
            patch.object(self.char1.sessions, "count", return_value=1),
            patch.object(cmdset_merge_warmup, "delay") as delay_mock,
        ):
            cmdset_merge_warmup.schedule_cmdset_merge_warmup_for_character(self.char1)
        delay_mock.assert_called_once()
        self.assertEqual(delay_mock.call_args.args[0], 0)

    def test_warm_all_skips_non_puppeted(self):
        from evennia.commands import cmdset_merge_warmup

        unpuppeted = MagicMock()
        unpuppeted.logged_in = True
        unpuppeted.get_puppet = MagicMock(return_value=None)
        fake_handler = MagicMock()
        fake_handler.get_sessions.return_value = [unpuppeted]
        with (
            patch.object(evennia, "SESSION_HANDLER", fake_handler),
            patch.object(cmdset_merge_warmup, "warm_cmdset_merge_for_session") as warm_mock,
        ):
            cmdset_merge_warmup.warm_all_logged_in_puppet_sessions()
        warm_mock.assert_not_called()


# --- bridge error surfacing (dispatch honesty) -------------------------------
class _BridgeErrCaller:
    """Minimal called_by: records what the player would see."""

    def __init__(self):
        self.key = "Crashee"
        self.messages = []

    def msg(self, text=None, **kwargs):
        self.messages.append(text)


class TestBridgeErrorSurfacing(TwistedTestCase):
    """An exception escaping the engine bridge must reach the player and be
    reported, never vanish into an unconsumed failed Deferred."""

    def test_bridge_exception_reports_untrapped_error(self):
        called_by = _BridgeErrCaller()
        out = {}
        with patch(
            "evennia.actions.dispatch.try_action_dispatch",
            side_effect=RuntimeError("bridge kaboom"),
        ):
            d = ensureDeferred(cmdhandler.cmdhandler(called_by, "kick goblin", callertype="object"))
            d.addCallbacks(
                lambda r: out.__setitem__("result", r),
                lambda f: out.__setitem__("fail", f),
            )
        self.assertNotIn("fail", out, "bridge exception escaped as a failed Deferred")
        joined = "\n".join(str(m) for m in called_by.messages)
        self.assertIn("untrapped error", joined.lower())

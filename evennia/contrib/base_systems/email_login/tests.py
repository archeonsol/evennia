"""
Test email login.

"""

from evennia.commands.default.tests import BaseEvenniaCommandTest

from . import email_login


class TestEmailLogin(BaseEvenniaCommandTest):
    def test_connect(self):
        # Only `create` prompts for confirmation (its func yields); `connect`
        # has no yes/no prompt, so an inputs=["Y"] there is a leftover that gets
        # executed as a spurious command.
        self.call(
            email_login.CmdUnconnectedConnect(),
            "mytest@test.com test",
            "The email 'mytest@test.com' does not match any accounts.",
        )
        self.call(
            email_login.CmdUnconnectedCreate(),
            '"mytest" mytest@test.com test11111',
            "A new account 'mytest' was created. Welcome!",
            inputs=["Y"],
        )
        self.call(
            email_login.CmdUnconnectedConnect(),
            "mytest@test.com test11111",
            "",
            caller=self.account.sessions.get()[0],
        )

    def test_quit(self):
        self.call(email_login.CmdUnconnectedQuit(), "", "", caller=self.account.sessions.get()[0])

    def test_unconnectedlook(self):
        self.call(email_login.CmdUnconnectedLook(), "", "==========")

    def test_unconnectedhelp(self):
        self.call(email_login.CmdUnconnectedHelp(), "", "You are not yet logged into the game.")

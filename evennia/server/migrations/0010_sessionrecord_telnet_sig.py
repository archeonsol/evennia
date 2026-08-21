"""Record the client's own telnet negotiation as a comparable column.

`client_fp` already hashed the negotiated capability set, but it includes the
client name, terminal type and encoding -- all of which a player sets. Evading
that fingerprint is a settings dialog.

This hashes only what the client's telnet stack chose to do: which options it
offered, in what order, and which subnegotiations it completed. Changing it
means changing client.

Additive. Existing rows get an empty value, which never matches anything: two
blanks are not the same client, and the detectors treat an empty signature as
no signal rather than as a match.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0009_sanctionproposal"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionrecord",
            name="telnet_sig",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="sessionrecord",
            index=models.Index(fields=["telnet_sig", "cidr"], name="server_sess_telnet__idx"),
        ),
    ]

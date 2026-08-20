"""Record the TLS handshake and the header order a web client presents.

Both are additive and both are empty by default.

`tls_sig` needs a reverse proxy to report the handshake it terminated, and is
empty until one does. `http_order_fp` needs nothing: the handshake already
arrives as an ordered list of header names.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("server", "0010_sessionrecord_telnet_sig"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionrecord",
            name="http_order_fp",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="sessionrecord",
            name="tls_sig",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="sessionrecord",
            index=models.Index(fields=["tls_sig", "cidr"], name="server_sess_tls_sig_idx"),
        ),
    ]

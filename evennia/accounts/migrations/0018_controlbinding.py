import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_remove_accountdb_db_attributes'),
        ('objects', '0018_remove_objectdb_db_attributes'),
    ]

    operations = [
        migrations.CreateModel(
            name='ControlBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('db_focus_stack', models.JSONField(blank=True, default=list, help_text='Ordered [[kind, id], ...]; floor=account, top=active body.')),
                ('db_generation', models.PositiveIntegerField(default=0, help_text='Bumps on every push/pop (the multi-session race guard).')),
                ('db_account', models.ForeignKey(help_text='The account that owns this control graph.', on_delete=django.db.models.deletion.CASCADE, related_name='control_bindings', to='accounts.accountdb')),
                ('db_identity', models.OneToOneField(blank=True, help_text='The persistent IC self (the character) this binding is for.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='control_binding', to='objects.objectdb')),
            ],
            options={
                'verbose_name': 'Control Binding',
                'verbose_name_plural': 'Control Bindings',
            },
        ),
    ]

# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2018-12-19 22:55
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('container', '0009_containerrun_submit_time'),
    ]

    operations = [
        migrations.AddField(
            model_name='containerrun',
            name='sandbox_purged',
            field=models.BooleanField(default=False, help_text=b'True if the sandbox has already been purged, False otherwise.'),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='sandbox_size',
            field=models.BigIntegerField(blank=True, help_text=b'Size of the sandbox in bytes.  If null, this has not been computed yet.', null=True),
        ),
    ]

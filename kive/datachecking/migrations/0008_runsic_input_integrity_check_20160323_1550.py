# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-03-23 22:50
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('archive', '0027_permissions_remove_null_20160203_1033'),
        ('datachecking', '0007_transition_SD_fks_20151117_1759'),
    ]

    operations = [
        migrations.AddField(
            model_name='integritychecklog',
            name='read_failed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='integritychecklog',
            name='runsic',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='input_integrity_check', to='archive.RunSIC'),
        ),
    ]

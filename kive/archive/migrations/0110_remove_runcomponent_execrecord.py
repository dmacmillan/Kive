# -*- coding: utf-8 -*-
# Generated by Django 1.11.21 on 2019-06-17 18:57
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('archive', '0109_drop_methodoutput_execlog_runstep'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='runcomponent',
            name='execrecord',
        ),
    ]
# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-05-09 23:10
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('archive', '0029_create_states'),
    ]

    operations = [
        migrations.AddField(
            model_name='run',
            name='_runstate',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to='archive.RunState'),
        ),
        migrations.AddField(
            model_name='runcomponent',
            name='_runcomponentstate',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to='archive.RunComponentState'),
        ),
    ]
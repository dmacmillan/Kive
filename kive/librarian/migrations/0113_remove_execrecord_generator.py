# -*- coding: utf-8 -*-
# Generated by Django 1.11.21 on 2019-06-14 23:45
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('librarian', '0112_remove_dataset_file_source'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='execrecord',
            name='generator',
        ),
    ]
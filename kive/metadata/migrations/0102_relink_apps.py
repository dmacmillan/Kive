# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-06-02 00:10
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('metadata', '0100_unlink_apps'),
        ('librarian', '0005_merge_dataset_SD_20151116_1012'),
        ('librarian', '0007_transition_SD_to_dataset_20151117_1748'),
    ]

    operations = [
        migrations.AlterField(
            model_name='datatype',
            name='prototype',
            field=models.OneToOneField(blank=True,
                                       null=True,
                                       on_delete=django.db.models.deletion.SET_NULL,
                                       related_name='datatype_modelled',
                                       to='librarian.Dataset'),
        ),
    ]
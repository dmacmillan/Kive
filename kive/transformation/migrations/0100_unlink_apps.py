# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-06-02 22:30
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transformation', '0010_xput_ordering_20160527_1449'),
    ]

    operations = [
        migrations.AlterField(
            model_name='xputstructure',
            name='compounddatatype',
            field=models.IntegerField(db_column='compounddatatype_id'),
        ),
    ]
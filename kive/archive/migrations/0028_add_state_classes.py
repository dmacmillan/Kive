# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-05-04 20:05
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('archive', '0027_permissions_remove_null_20160203_1033'),
    ]

    operations = [
        migrations.CreateModel(
            name='RunComponentState',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('description', models.TextField()),
            ],
        ),
        migrations.CreateModel(
            name='RunState',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('description', models.TextField()),
            ],
        ),
    ]
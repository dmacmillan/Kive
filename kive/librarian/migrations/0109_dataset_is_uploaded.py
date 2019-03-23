# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-03-20 21:25
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('librarian', '0108_dataset_is_external_missing'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='is_uploaded',
            field=models.BooleanField(default=True, help_text='True if the file was uploaded, not an output.'),
        ),
    ]
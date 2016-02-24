# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-02-03 18:33
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('librarian', '0010_dataset_file_not_null'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dataset',
            name='groups_allowed',
            field=models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='librarian_dataset_has_access_to', to='auth.Group'),
        ),
        migrations.AlterField(
            model_name='dataset',
            name='users_allowed',
            field=models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='librarian_dataset_has_access_to', to=settings.AUTH_USER_MODEL),
        ),
    ]
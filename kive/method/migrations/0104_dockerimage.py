# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2017-10-26 21:04
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0007_alter_validators_add_error_messages'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('method', '0103_method_memory'),
    ]

    operations = [
        migrations.CreateModel(
            name='DockerImage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Docker image name', max_length=200, verbose_name='Name')),
                ('tag', models.CharField(help_text='Docker image tag', max_length=128, verbose_name='Tag')),
                ('git', models.CharField(help_text='URL of Git repository', max_length=2000, verbose_name='Git URL')),
                ('description', models.CharField(blank=True, help_text='What is this image used for?', max_length=2000, verbose_name='Description')),
                ('created', models.DateTimeField(auto_now_add=True, help_text='When this docker image was added to Kive.')),
                ('groups_allowed', models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='method_dockerimage_has_access_to', to='auth.Group')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('users_allowed', models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='method_dockerimage_has_access_to', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
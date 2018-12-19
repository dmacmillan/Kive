# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2018-11-23 19:10
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('container', '0003_reverse_ordering'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContainerApp',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, help_text=b'Leave blank for default', max_length=60)),
                ('description', models.CharField(blank=True, max_length=1000, verbose_name=b'Description')),
                ('container', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='apps', to='container.Container')),
            ],
            options={
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='ContainerArgument',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('position', models.IntegerField(blank=True, help_text=b'Position in the arguments (gaps and duplicates are allowed). Leave position blank to pass as an option with --name.', null=True)),
                ('type', models.CharField(choices=[(b'I', b'Input'), (b'O', b'Output')], max_length=1)),
                ('allow_multiple', models.BooleanField(default=False, help_text=b'True for optional inputs that accept multiple datasets and outputs that just collect all files written to a directory')),
                ('app', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='arguments', to='container.ContainerApp')),
            ],
        ),
    ]
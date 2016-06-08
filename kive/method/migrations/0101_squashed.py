# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-06-01 18:24
from __future__ import unicode_literals

import os.path

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    replaces = [(b'method', '0001_initial'),
                (b'method', '0002_auto_20150204_1703'),
                (b'method', '0003_auto_20150213_1703'),
                (b'method', '0004_auto_20150216_1641'),
                (b'method', '0005_redacted_20150417_1128'),
                (b'method', '0006_add_method_crr_related_name_20150417_1656'),
                (b'method', '0007_method_ordering_20150601_1348'),
                (b'method', '0008_coderesourcerevision_ordering_20150604_1501'),
                (b'method', '0009_coderesourcerevision_revision_number_positive_20150604_1553'),
                (b'method', '0010_coderesourcerevision_unique_together_20150604_1623'),
                (b'method', '0011_permissions_remove_null_20160203_1033'),
                (b'method', '0012_methoddependency'),
                (b'method', '0013_transfer_crd_to_md_20160224_1627'),
                (b'method', '0014_remove_crd_20160225_1602'),
                (b'method', '0015_eliminate_empty_crr_add_default_20160225_1616'),
                (b'method', '0016_eliminate_empty_crr_remove_default_20160225_1619')]

    initial = True

    dependencies = [
        ('auth', '0001_initial'),
        ('transformation', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CodeResource',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='The name for this resource and all subsequent revisions.', max_length=60, unique=True, verbose_name='Resource name')),
                ('filename', models.CharField(help_text='The filename for this resource', max_length=260, validators=[django.core.validators.RegexValidator(message='Invalid code resource filename', regex='^(\x08|([-_.()\\w]+ *)*[-_.()\\w]+)$')], verbose_name='Resource file name')),
                ('description', models.TextField(blank=True, max_length=1000, verbose_name='Resource description')),
            ],
            options={
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='CodeResourceRevision',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision_number', models.IntegerField(blank=True, help_text='Revision number of code resource', verbose_name='Revision number')),
                ('revision_name', models.CharField(blank=True, help_text='A name to differentiate revisions of a CodeResource', max_length=60)),
                ('revision_DateTime', models.DateTimeField(auto_now_add=True, help_text='Date this resource revision was uploaded')),
                ('revision_desc', models.TextField(blank=True, help_text='A description for this particular resource revision', max_length=1000, verbose_name='Revision description')),
                ('content_file', models.FileField(help_text='File contents of this code resource revision', upload_to='CodeResources', verbose_name='File contents')),
                ('MD5_checksum', models.CharField(blank=True, help_text='Used to validate file contents of this resource revision', max_length=64)),
                ('coderesource', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revisions', to='method.CodeResource')),
                ('revision_parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='descendants', to='method.CodeResourceRevision')),
            ],
        ),
        migrations.CreateModel(
            name='Method',
            fields=[
                ('transformation_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='transformation.Transformation')),
                ('revision_number', models.PositiveIntegerField(blank=True, help_text='Revision number of this Method in its family', verbose_name='Method revision number')),
                ('reusable', models.PositiveSmallIntegerField(choices=[(1, 'deterministic'), (2, 'reusable'), (3, 'non-reusable')], default=1, help_text='Is the output of this method the same if you run it again with the same inputs?\n\ndeterministic: always exactly the same\n\nreusable: the same but with some insignificant differences (e.g., rows are shuffled)\n\nnon-reusable: no -- there may be meaningful differences each time (e.g., timestamp)\n')),
                ('tainted', models.BooleanField(default=False, help_text='Is this Method broken?')),
                ('threads', models.PositiveIntegerField(default=1, help_text='How many threads does this Method use during execution?', validators=[django.core.validators.MinValueValidator(1)], verbose_name='Number of threads')),
                ('driver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='method.CodeResourceRevision')),
            ],
            bases=('transformation.transformation',),
        ),
        migrations.CreateModel(
            name='MethodFamily',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='The name given to a group of methods/pipelines', max_length=60, verbose_name='Transformation family name')),
                ('description', models.TextField(blank=True, help_text='A description for this collection of methods/pipelines', max_length=1000, verbose_name='Transformation family description')),
                ('groups_allowed', models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='method_methodfamily_has_access_to', to=b'auth.Group')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('users_allowed', models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='method_methodfamily_has_access_to', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('name',),
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='method',
            name='family',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='method.MethodFamily'),
        ),
        migrations.AddField(
            model_name='method',
            name='revision_parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='descendants', to='method.Method'),
        ),
        migrations.AlterField(
            model_name='method',
            name='driver',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='methods', to='method.CodeResourceRevision'),
        ),
        migrations.AlterUniqueTogether(
            name='method',
            unique_together=set([('family', 'revision_number')]),
        ),
        migrations.AddField(
            model_name='coderesource',
            name='groups_allowed',
            field=models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='method_coderesource_has_access_to', to=b'auth.Group'),
        ),
        migrations.AddField(
            model_name='coderesource',
            name='user',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='coderesource',
            name='users_allowed',
            field=models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='method_coderesource_has_access_to', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='coderesourcerevision',
            name='groups_allowed',
            field=models.ManyToManyField(blank=True, help_text='What groups have access?', null=True, related_name='method_coderesourcerevision_has_access_to', to=b'auth.Group'),
        ),
        migrations.AddField(
            model_name='coderesourcerevision',
            name='user',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='coderesourcerevision',
            name='users_allowed',
            field=models.ManyToManyField(blank=True, help_text='Which users have access?', null=True, related_name='method_coderesourcerevision_has_access_to', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='methodfamily',
            unique_together=set([('name', 'user')]),
        ),
        migrations.AlterField(
            model_name='coderesourcerevision',
            name='revision_parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='descendants', to='method.CodeResourceRevision'),
        ),
        migrations.AlterModelOptions(
            name='method',
            options={'ordering': ['family__name', '-revision_number']},
        ),
        migrations.AlterModelOptions(
            name='coderesourcerevision',
            options={'ordering': ['coderesource__name', '-revision_number']},
        ),
        migrations.AlterField(
            model_name='coderesourcerevision',
            name='revision_number',
            field=models.PositiveIntegerField(blank=True, help_text='Revision number of code resource', verbose_name='Revision number'),
        ),
        migrations.AlterField(
            model_name='coderesourcerevision',
            name='groups_allowed',
            field=models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='method_coderesourcerevision_has_access_to', to=b'auth.Group'),
        ),
        migrations.AlterField(
            model_name='coderesourcerevision',
            name='users_allowed',
            field=models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='method_coderesourcerevision_has_access_to', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='coderesourcerevision',
            unique_together=set([('coderesource', 'revision_number')]),
        ),
        migrations.CreateModel(
            name='MethodDependency',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(blank=True, help_text='Where a dependency must exist in the sandbox', max_length=255, verbose_name='Dependency path')),
                ('filename', models.CharField(blank=True, help_text='The file name the dependency is given in the sandbox at execution', max_length=255, verbose_name='Dependency file name')),
                ('method', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dependencies', to='method.Method')),
                ('requirement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='used_by', to='method.CodeResourceRevision')),
            ],
        ),
    ]

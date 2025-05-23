# Generated by Django 5.1.7 on 2025-04-17 06:58

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0006_merge_20250401_2018'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='user',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='user_profile',
                                       to=settings.AUTH_USER_MODEL),
        ),
    ]

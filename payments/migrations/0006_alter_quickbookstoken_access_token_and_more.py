# Generated by Django 5.2 on 2025-04-28 12:34

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('payments', '0005_merge_20250428_1056'),
    ]

    operations = [
        migrations.AlterField(
            model_name='quickbookstoken',
            name='access_token',
            field=models.CharField(),
        ),
        migrations.AlterField(
            model_name='quickbookstoken',
            name='refresh_token',
            field=models.CharField(),
        ),
    ]

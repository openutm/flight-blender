# Generated by Django 5.1.5 on 2025-04-20 13:41

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flight_declaration_operations', '0013_flightoperationalintentdetail_subscribers_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='peercompositeoperationalintent',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='peercompositeoperationalintent',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]

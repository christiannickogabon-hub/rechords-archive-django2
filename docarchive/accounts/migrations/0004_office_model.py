"""Offices become their own records; each user's typed office text is carried over."""

import django.db.models.deletion
from django.db import migrations, models


def text_to_offices(apps, schema_editor):
    Office = apps.get_model("accounts", "Office")
    User = apps.get_model("accounts", "User")
    for user in User.objects.exclude(office="").exclude(office=None):
        name = user.office.strip()
        if not name:
            continue
        office = Office.objects.filter(name__iexact=name).first()
        if office is None:
            office = Office.objects.create(name=name)
        User.objects.filter(pk=user.pk).update(office_ref=office)


def offices_to_text(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.exclude(office_ref=None).select_related("office_ref"):
        User.objects.filter(pk=user.pk).update(office=user.office_ref.name)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_super_admin_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="Office",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="Office or division")),
                ("description", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="user",
            name="office_ref",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="members", to="accounts.office", verbose_name="Office or division",
            ),
        ),
        migrations.RunPython(text_to_offices, offices_to_text),
        migrations.RemoveField(model_name="user", name="office"),
        migrations.RenameField(model_name="user", old_name="office_ref", new_name="office"),
    ]

"""Seed the four built-in series as folders.

Their codes match the old DocumentType values exactly, so every record already
filed keeps its control number and its place on the overview.
"""
from django.db import migrations

BUILTINS = [
    # code, label, long name, icon, tone, order
    ("MEMO", "Memorandum", "MEMORANDUM", "icon-file-signature", "green", 10),
    ("IND", "Endorsement", "ENDORSEMENT", "icon-file-check", "teal", 20),
    ("OO", "Office Order", "OFFICE ORDER", "icon-clipboard-list", "amber", 30),
    ("EO", "Executive Order", "EXECUTIVE ORDER", "icon-scroll-text", "indigo", 40),
]


def seed(apps, schema_editor):
    DocumentFolder = apps.get_model("documents", "DocumentFolder")
    for code, label, long_name, icon, tone, order in BUILTINS:
        DocumentFolder.objects.update_or_create(
            code=code,
            defaults={
                "label": label,
                "long_name": long_name,
                "icon": icon,
                "tone": tone,
                "sort_order": order,
                "is_builtin": True,
            },
        )


def unseed(apps, schema_editor):
    DocumentFolder = apps.get_model("documents", "DocumentFolder")
    DocumentFolder.objects.filter(code__in=[row[0] for row in BUILTINS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0011_alter_document_doc_type_documentfolder"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

from datetime import date

from django import forms
from django.conf import settings

from .importing.common import validate_upload_file
from .models import (Attachment, Document, DocumentFolder, DocumentType,
                     folder_choices)


class DocumentForm(forms.ModelForm):
    remove_file = forms.BooleanField(
        required=False, label="Remove the attached file",
    )

    class Meta:
        model = Document
        fields = [
            "doc_type", "series_year", "number",
            "subject", "description", "date_issued", "effectivity_date",
            "originating_office", "signatory", "addressee", "keywords",
            "status", "classification",
            "file", "physical_location",
            "related_to", "indorsement_level",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 6}),
            "date_issued": forms.DateInput(attrs={"type": "date"}),
            "effectivity_date": forms.DateInput(attrs={"type": "date"}),
            "subject": forms.TextInput(attrs={"placeholder": "Subject line as written on the document"}),
            "keywords": forms.TextInput(attrs={"placeholder": "budget, travel order, 2026"}),
            "physical_location": forms.TextInput(attrs={"placeholder": "Cabinet 3, Drawer B"}),
            # The stock clearable widget prints the whole storage path; the template
            # shows the current file and a tidy "remove" checkbox instead.
            "file": forms.FileInput,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["related_to"].queryset = Document.objects.filter(is_deleted=False)
        # the folders an administrator has opened, not a frozen list
        self.fields["doc_type"] = forms.ChoiceField(
            label="Document type", choices=folder_choices(),
        )
        self.fields["number"].required = False
        if not self.instance.pk or not self.instance.file:
            self.fields.pop("remove_file")
        if not self.instance.pk:
            self.fields["number"].help_text = (
                "Leave blank and the next number in this series is assigned on save."
            )

    def clean_series_year(self):
        year = self.cleaned_data["series_year"]
        if not (1900 <= year <= date.today().year + 1):
            raise forms.ValidationError("Enter a series year between 1900 and next year.")
        return year

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if not upload or not hasattr(upload, "size"):
            return upload
        problems = validate_upload_file(upload)
        if problems:
            raise forms.ValidationError(problems[0])
        return upload

    def clean(self):
        cleaned = super().clean()
        doc_type = cleaned.get("doc_type")
        number = cleaned.get("number")
        year = cleaned.get("series_year")

        if doc_type == DocumentType.INDORSEMENT and not cleaned.get("indorsement_level"):
            self.add_error("indorsement_level", "Say which endorsement this is: 1, 2, 3 …")
        if doc_type != DocumentType.INDORSEMENT:
            cleaned["indorsement_level"] = None

        if doc_type and number and year:
            clash = Document.objects.filter(
                doc_type=doc_type, series_year=year, number=number
            ).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(
                    "number",
                    f"{clash.first().control_number} is already on file. Pick another number.",
                )

        related = cleaned.get("related_to")
        if related and self.instance.pk and related.pk == self.instance.pk:
            self.add_error("related_to", "A document cannot refer to itself.")
        return cleaned

    def save(self, commit=True):
        document = super().save(commit=False)
        if self.cleaned_data.get("remove_file") and not self.cleaned_data.get("file"):
            document.file = None
        if commit:
            document.save()
        return document


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["label", "file"]

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if upload and upload.size > settings.MAX_UPLOAD_SIZE:
            limit = settings.MAX_UPLOAD_SIZE // (1024 * 1024)
            raise forms.ValidationError(f"That file is larger than the {limit} MB limit.")
        return upload


class DocumentFolderForm(forms.ModelForm):
    """Opens a new series. The code is what gets stored on every record filed
    under it, so it can only be set once."""

    class Meta:
        model = DocumentFolder
        fields = ["label", "code", "long_name", "icon", "tone"]
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "e.g. Resolution"}),
            "code": forms.TextInput(attrs={"placeholder": "e.g. RES", "maxlength": 8}),
            "long_name": forms.TextInput(attrs={"placeholder": "e.g. RESOLUTION"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["long_name"].required = False
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = (
                "The code is fixed once records are filed under the folder."
            )

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if self.instance.pk:
            return self.instance.code
        if not code.isalnum():
            raise forms.ValidationError("Use letters and digits only, e.g. RES.")
        if DocumentFolder.objects.filter(code=code).exists():
            raise forms.ValidationError("A folder already uses that code.")
        return code

    def clean_label(self):
        label = self.cleaned_data["label"].strip()
        clash = DocumentFolder.objects.filter(label__iexact=label).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A folder already goes by that name.")
        return label

    def clean_long_name(self):
        return (self.cleaned_data.get("long_name") or "").strip().upper()


class DocumentSearchForm(forms.Form):
    """Every field is optional; blanks are ignored."""

    q = forms.CharField(
        required=False, label="Keyword",
        widget=forms.TextInput(attrs={
            "placeholder": "Subject, office, signatory, tag…",
            "autofocus": True, "autocomplete": "off",
        }),
    )
    doc_type = forms.ChoiceField(required=False, label="Type", choices=[])
    number = forms.IntegerField(required=False, min_value=1, label="No.")
    series_year = forms.IntegerField(required=False, min_value=1900, label="Series")
    status = forms.ChoiceField(
        required=False, label="Status",
        choices=[("", "Any status")] + list(Document.Status.choices),
    )
    office = forms.CharField(required=False, label="Originating office")
    date_from = forms.DateField(
        required=False, label="Issued from", widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        required=False, label="Issued to", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["doc_type"].choices = [("", "All types")] + folder_choices()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The end date falls before the start date.")
        return cleaned

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        qs = queryset.search(data.get("q"))
        if data.get("doc_type"):
            qs = qs.filter(doc_type=data["doc_type"])
        if data.get("number"):
            qs = qs.filter(number=data["number"])
        if data.get("series_year"):
            qs = qs.filter(series_year=data["series_year"])
        if data.get("status"):
            qs = qs.filter(status=data["status"])
        if data.get("office"):
            qs = qs.filter(originating_office__icontains=data["office"])
        if data.get("date_from"):
            qs = qs.filter(date_issued__gte=data["date_from"])
        if data.get("date_to"):
            qs = qs.filter(date_issued__lte=data["date_to"])
        return qs

    @property
    def is_filtered(self):
        return any(self.data.get(name) for name in self.fields)

    def summary(self):
        if not self.is_valid():
            return ""
        bits = []
        for name, value in self.cleaned_data.items():
            if value:
                bits.append(f"{name}={value}")
        return "; ".join(bits)

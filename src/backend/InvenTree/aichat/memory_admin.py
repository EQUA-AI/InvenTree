"""Explicit client enrollment through the existing tenant administration page."""

from django import forms
from django.contrib import admin
from django.utils import timezone

from ai.core.config import get_settings
from aichat.models import ClientAISettings
from aichat.services.memory_controls import NOTICE_COPY
from aichat.services.memory_eligibility import notice_number
from InvenTree.restore_hold import restore_hold_enabled


class ClientMemoryForm(forms.ModelForm):
    """A client cannot require a notice newer than the available copy."""

    class Meta:
        """Only the independent enrollment gate and minimum version are editable."""

        model = ClientAISettings
        fields = ['memory_enabled', 'required_notice_version']

    def clean(self):
        """Fail closed on holds, missing copy and invalid minimum versions."""
        data = super().clean()
        if restore_hold_enabled():
            raise forms.ValidationError('Memory settings are held during restore.')
        current = get_settings().aimms_memory_notice_version
        required = data.get('required_notice_version') or current
        if data.get('memory_enabled'):
            try:
                valid = current in NOTICE_COPY and notice_number(
                    required
                ) <= notice_number(current)
            except ValueError:
                valid = False
            if not valid:
                raise forms.ValidationError(
                    'The required memory notice is not available.'
                )
            data['required_notice_version'] = required
            # Model validation sees a coherent proposed enrollment too.
            self.instance.enabled_at = self.instance.enabled_at or timezone.now()
        return data


class ClientMemoryInline(admin.StackedInline):
    """Django enforces ClientAISettings add/change permission on this inline."""

    model = ClientAISettings
    form = ClientMemoryForm
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ['enabled_by', 'enabled_at', 'updated_at']

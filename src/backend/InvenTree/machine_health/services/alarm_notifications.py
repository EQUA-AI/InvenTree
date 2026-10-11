"""Tell somebody when a critical alarm opens.

Until now an alarm was visible only to whoever happened to be looking at the
page it was on. A critical condition on a pump at three in the morning was
recorded, evaluated and drawn, and nobody was told. This sends it through the
same notification framework the rest of the system uses - the bell in the web
app, and email for users who have it switched on - to the people who could act
on it.

Who those are is decided by the two gates the Health tab itself enforces, and
no others: the ``work_order`` role, which is what the health endpoints require,
and a scope grant on the machine's client, which is what lets them read it.
Anyone who could open the page is told; nobody who could not is.

Only a critical is sent, and only once per condition: when it opens as one, or
when a warning is confirmed up to one. A warning is a reading to keep an eye
on, and with provisional limits on an estate of fourteen-pump stations it would
be a stream; a critical is a condition to go and look at.

And at most once an hour for any one signal. A reading sitting on its limit
closes after five minutes inside and opens again at the next breach, each time
a new condition; a condition is worth telling, a signal that cannot make up
its mind is not, and an hour is long enough to notice the pattern on the page.

A user can switch these off (``NOTIFY_MACHINE_ALARMS``); the page still shows
them everything.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model

import structlog
from tasks.scope import ScopeError

import common.models
import common.notifications
import InvenTree.helpers
import InvenTree.helpers_model
from assets.health_models import AnomalySeverity
from assets.registry_api import authorized_client_ids
from users.permissions import check_user_role

logger = structlog.get_logger('inventree')

CATEGORY = 'machine_health.critical_alarm'
TEMPLATE = 'email/machine_alarm.html'
#: How long one signal stays quiet after being announced, however many
#: conditions it opens in the meantime.
SIGNAL_QUIET = timedelta(hours=1)


def recipients_for(machine):
    """The users who could open this machine's Health tab, and want to hear."""
    users = []
    for user in get_user_model().objects.filter(is_active=True).order_by('pk'):
        if not check_user_role(user, 'work_order', 'view'):
            continue
        if not common.models.InvenTreeUserSetting.get_setting(
            'NOTIFY_MACHINE_ALARMS', True, user=user
        ):
            continue
        try:
            clients = authorized_client_ids(user)
        except ScopeError:
            continue
        if machine.client_id in clients:
            users.append(user)
    return users


def notify_critical(anomaly):
    """Send one notification for a condition that is now critical.

    Nothing here can fail the evaluation that called it: a notification that
    cannot be sent is logged and the anomaly stands regardless.
    """
    if anomaly.severity != AnomalySeverity.CRITICAL:
        return
    machine = anomaly.machine
    # One quiet period per signal on a machine: the fingerprint is the signal's
    # identity, the same across every condition it opens.
    signal = f'{CATEGORY}:{anomaly.fingerprint}'
    try:
        if common.models.NotificationEntry.check_recent(
            signal, machine.pk, SIGNAL_QUIET
        ):
            logger.info(
                'Critical alarm not re-announced within the quiet period',
                anomaly=anomaly.pk,
                machine=machine.pk,
            )
            return
        users = recipients_for(machine)
        if not users:
            logger.info(
                'No recipient for critical alarm',
                anomaly=anomaly.pk,
                machine=machine.pk,
            )
            return
        path = InvenTree.helpers.pui_url(f'/machines/machine/{machine.pk}/health')
        name = f'Critical alarm: {machine.name}'
        common.notifications.trigger_notification(
            anomaly,
            CATEGORY,
            targets=users,
            context={
                'name': name,
                'message': anomaly.title,
                'link': InvenTree.helpers_model.construct_absolute_url(path),
                'anomaly': anomaly,
                'machine': machine,
                'template': {'html': TEMPLATE, 'subject': name},
            },
        )
        # The framework records what it delivered, per anomaly. The signal's
        # quiet period starts only from a delivery that happened.
        if common.models.NotificationEntry.objects.filter(
            key=CATEGORY, uid=anomaly.pk
        ).exists():
            common.models.NotificationEntry.notify(signal, machine.pk)
    except Exception:  # pragma: no cover - transport failure
        logger.exception('Critical alarm notification failed', anomaly=anomaly.pk)

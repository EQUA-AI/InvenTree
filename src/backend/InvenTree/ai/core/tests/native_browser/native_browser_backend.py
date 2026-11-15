"""Serve only an owned synthetic native backend for full-application browser tests."""

import ast
import json
import os
import socket
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4


def main():
    """Use actual native URLs, authentication, scope, permissions and serializers."""
    root = Path(__file__).resolve().parents[7]
    runtime = Path(os.environ["AIMMS_NATIVE_BROWSER_RUNTIME"]).resolve(strict=True)
    restoring = os.environ.get("AIMMS_NATIVE_BROWSER_RESTORE") == "1"
    hidden = Path(os.environ["AIMMS_HIDDEN_CHECKOUT"])
    if hidden.exists() or not runtime.is_relative_to(Path("/tmp")):
        raise RuntimeError("The real checkout must be hidden and runtime owned")
    sys.path[:0] = [
        str(root / "src/backend"),
        str(root / "src/backend/InvenTree"),
        str(Path(__file__).resolve().parent),
    ]
    os.environ["DJANGO_SETTINGS_MODULE"] = "ai.core.tests.settings"
    sys.argv = ["manage.py", "test"]

    # Only the isolated localhost Vite/ASGI/browser path may open a connection.
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if not isinstance(address, (tuple, str)) or (
            isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost")
        ):
            raise RuntimeError("External network is unavailable in browser fixtures")
        return original_connect(sock, address)

    socket.socket.connect = local_connect
    from aimms_flags import django_flags
    from django.conf import settings

    for entry in django_flags():
        if not hasattr(settings, entry.env_name):
            value = entry.default
            if entry.kind == "csv":
                value = [part.strip() for part in str(value or "").split(",") if part.strip()]
            setattr(settings, entry.env_name, value)

    extras = (
        "django_mailbox",
        "flags",
        "django_otp",
        "django_otp.plugins.otp_static",
        "django_otp.plugins.otp_totp",
        "integration_state.apps.IntegrationStateConfig",
    )
    settings.INSTALLED_APPS = [
        *settings.INSTALLED_APPS,
        *(name for name in extras if name not in settings.INSTALLED_APPS),
    ]
    native = ast.parse((root / "src/backend/InvenTree/InvenTree/settings.py").read_bytes())
    for node in native.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REST_FRAMEWORK"
            for target in node.targets
        ):
            settings.REST_FRAMEWORK = ast.literal_eval(node.value)
    settings.DATABASES["default"]["NAME"] = str(runtime / "native-browser.sqlite3")
    settings.DATABASES["default"]["TEST"] = {"NAME": str(runtime / "native-browser-test.sqlite3")}
    settings.ROOT_URLCONF = "InvenTree.urls"
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:56311", "http://localhost:8000"]
    settings.CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:56311"]
    settings.CORS_ALLOW_CREDENTIALS = True
    settings.MEDIA_ROOT = str(runtime / "media")
    settings.STATIC_ROOT = str(runtime / "static")
    settings.Q_CLUSTER = {**settings.Q_CLUSTER, "orm": "default", "sync": False}
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
    settings.AIMMS_MAINTENANCE_SCOPE_RESOLVER = "tasks.scope.granted_client_scope_resolver"
    settings.AIMMS_SINGLE_SITE_CLIENT_CODE = "absent-native-browser-fallback"
    settings.AIMMS_WORK_ORDERS_ENABLED = True
    settings.AIMMS_INTEGRATION_AUTHORITY_ENABLED = False
    settings.AIMMS_PLANT_TIMEZONE = ""
    settings.MIDDLEWARE = [
        "corsheaders.middleware.CorsMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "allauth.account.middleware.AccountMiddleware",
    ]
    from oauth2_provider import settings as oauth_defaults

    for name in (
        "APPLICATION",
        "DEVICE_GRANT",
        "ACCESS_TOKEN",
        "ID_TOKEN",
        "GRANT",
        "REFRESH_TOKEN",
    ):
        setattr(
            settings,
            f"OAUTH2_PROVIDER_{name}_MODEL",
            getattr(oauth_defaults, f"{name}_MODEL"),
        )
    import django

    django.setup()
    from django.apps import apps
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.core.management import call_command

    settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
    if not restoring:
        call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
    from assets.models import (
        AssetMachine,
        AssetMaintenanceRecord,
        Client,
        ClientScopeGrant,
    )
    from tasks.models import WorkOrder
    from users.models import RuleSet

    if restoring:
        prior = json.loads((runtime / "fixture-info.json").read_text())
        users, machines, work, maintenance = (
            prior[k] for k in ("users", "machines", "work", "maintenance")
        )
    else:
        group = Group.objects.create(name="Synthetic native browser readers")
        RuleSet.objects.update_or_create(
            group=group, name="work_order", defaults={"can_view": True}
        )
        users = []
        machines = []
        work = []
        maintenance = []
        for code in ("a", "b"):
            user = get_user_model().objects.create_user(
                username=f"native-browser-{code}", password="synthetic-browser-only"
            )
            user.groups.add(group)
            client = Client.objects.create(
                name=f"Synthetic Browser {code.upper()}", code=f"browser-{code}"
            )
            ClientScopeGrant.objects.create(user=user, client=client)
            station = AssetMachine.objects.create(
                name=f"Browser Fixture Station {code.upper()}",
                client=client,
                asset_type="pumphouse",
                source_namespace="synthetic-browser",
                source_entity_uuid=uuid4(),
            )
            machine = AssetMachine.objects.create(
                name=f"Browser Fixture Machine {code.upper()}",
                client=client,
                asset_type="pump",
                parent=station,
                source_key=f"synthetic/pump-{code}",
            )
            order = WorkOrder.objects.create(
                title=f"Browser Fixture Work {code.upper()}",
                machine=machine,
                status=WorkOrder.STATUS_BACKLOG,
                priority=WorkOrder.PRIORITY_MEDIUM,
            )
            record = AssetMaintenanceRecord.objects.create(
                machine=machine,
                work_order=order,
                date=date(2025, 1, 2),
                summary=f"Browser Fixture Maintenance {code.upper()}",
            )
            users.append(user.pk)
            machines.append(machine.pk)
            work.append(order.pk)
            maintenance.append(record.pk)
    from source_browser_runtime import install_fixture

    fixture_source = install_fixture(runtime, users, machines, restoring=restoring)
    info = {
        "process_id": os.getpid(),
        "restored": restoring,
        "source": fixture_source,
        "users": users,
        "machines": machines,
        "work": work,
        "maintenance": maintenance,
        "profile": "Synthetic SQLite model-sync; actual native URLs/auth/scope/serializers; explicit fixture middleware; fixture-owned model-free AI initialization; no production readiness claim",
    }
    (runtime / "fixture-info.json").write_text(json.dumps(info, indent=2) + "\n")

    def snapshot():
        return {
            "machines": list(AssetMachine.objects.order_by("pk").values("pk", "name", "client_id")),
            "work": list(
                WorkOrder.objects.order_by("pk").values(
                    "pk", "title", "status", "lifecycle_version"
                )
            ),
            "maintenance": list(
                AssetMaintenanceRecord.objects.order_by("pk").values(
                    "pk", "machine_id", "work_order_id", "summary"
                )
            ),
        }

    placement_file = runtime / "fixture-start-placement.json"
    if not restoring:
        placement_file.write_text(
            json.dumps(
                list(
                    AssetMachine.objects.order_by("pk").values(
                        "pk", "physical_location_id", "placement_version"
                    )
                ),
                indent=2,
            )
            + "\n"
        )
    before = snapshot()
    baseline_file = runtime / "fixture-start-snapshot.json"
    if restoring:
        before = json.loads(baseline_file.read_text())
        if snapshot() != before:
            raise RuntimeError("Native business rows changed before process restoration")
    else:
        baseline_file.write_text(json.dumps(before, indent=2) + "\n")
    starts_file = runtime / "fixture-startups.json"
    starts = json.loads(starts_file.read_text()) if starts_file.exists() else []
    starts.append({
        "pid": os.getpid(),
        "restored": restoring,
        "users": users,
        "machines": machines,
    })
    starts_file.write_text(json.dumps(starts, indent=2) + "\n")
    import uvicorn

    try:
        uvicorn.run(
            "InvenTree.asgi:application",
            host="127.0.0.1",
            port=8000,
            lifespan="on",
            access_log=False,
        )
    finally:
        after = snapshot()
        placement_before = json.loads(placement_file.read_text())
        placement_after = list(
            AssetMachine.objects.order_by("pk").values(
                "pk", "physical_location_id", "placement_version"
            )
        )
        placement_config = json.loads((runtime / "fixture-source-state.json").read_text()).get(
            "placement"
        )
        expected_placement = [dict(row) for row in placement_before]
        if placement_config:
            moved = next(
                row for row in expected_placement if row["pk"] == placement_config["machine_pk"]
            )
            moved["physical_location_id"] = placement_config["destination_pk"]
            moved["placement_version"] += 1
        (runtime / "fixture-readback.json").write_text(
            json.dumps(
                {
                    "placement_before": placement_before,
                    "placement_after": placement_after,
                    "authorized_placement_write_verified": placement_after == expected_placement,
                    "before": before,
                    "after": after,
                    "selected_operational_rows_unchanged": before == after,
                    "limits": "Selected name/client/Work/maintenance fields unchanged. Separate exact placement comparison allows only the explicitly exercised one native transfer/replay; fixture role/projection/location, session/chat and authority writes are intentional. Not an all-table side-effect proof.",
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()

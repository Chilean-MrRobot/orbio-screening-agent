"""Sube a Secret Manager los secretos del `.env` (idempotente; nunca imprime valores).

Uso: python deploy/sync_secrets.py --project <id> [--env .env]
Crea el secreto si no existe y agrega una versión solo si el valor cambió. Un secreto
obligatorio sin valor en el `.env` recibe uno provisorio al azar (p. ej. el app secret de
WhatsApp antes de configurar Meta), para que el servicio arranque.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys

from dotenv import dotenv_values

# variable del .env → (nombre del secreto, ¿provisorio si falta?)
SECRETS: dict[str, tuple[str, bool]] = {
    "OPENAI_API_KEY": ("openai-api-key", False),
    "PANEL_PASSWORD": ("panel-password", True),
    "TASKS_TOKEN": ("tasks-token", True),
    "REENGAGEMENT_TASK_TOKEN": ("reengagement-task-token", True),
    "WHATSAPP_VERIFY_TOKEN": ("whatsapp-verify-token", True),
    "WHATSAPP_APP_SECRET": ("whatsapp-app-secret", True),
    "WHATSAPP_ACCESS_TOKEN": ("whatsapp-access-token", False),
    "LANGSMITH_API_KEY": ("langsmith-api-key", False),
}


def gcloud(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    exe = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
    return subprocess.run([exe, *args], input=stdin, capture_output=True, text=True)


def current_value(project: str, name: str) -> str | None:
    result = gcloud(
        "secrets", "versions", "access", "latest", f"--secret={name}", f"--project={project}"
    )
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()

    env = dotenv_values(args.env)
    for variable, (name, placeholder) in SECRETS.items():
        value = (env.get(variable) or "").strip()
        if not value:
            if not placeholder:
                print(f"{name}: sin valor en {args.env}, se omite")
                continue
            if current_value(args.project, name) is not None:
                print(f"{name}: sin valor en {args.env}, se mantiene la versión actual")
                continue
            value = secrets.token_urlsafe(32)
            print(f"{name}: sin valor en {args.env}, se sube uno PROVISORIO")

        exists = gcloud("secrets", "describe", name, f"--project={args.project}").returncode == 0
        if not exists:
            created = gcloud(
                "secrets",
                "create",
                name,
                f"--project={args.project}",
                "--replication-policy=user-managed",
                "--locations=europe-west1",
                "--data-file=-",
                stdin=value,
            )
            print(
                f"{name}: creado"
                if created.returncode == 0
                else f"{name}: ERROR {created.stderr[-300:]}"
            )
        elif current_value(args.project, name) != value:
            added = gcloud(
                "secrets",
                "versions",
                "add",
                name,
                f"--project={args.project}",
                "--data-file=-",
                stdin=value,
            )
            print(
                f"{name}: nueva versión"
                if added.returncode == 0
                else f"{name}: ERROR {added.stderr[-300:]}"
            )
        else:
            print(f"{name}: sin cambios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

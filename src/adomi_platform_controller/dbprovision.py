"""Provision a logical database and login role inside a DatabaseServer.

A :class:`~..handlers.database.DatabaseReconciler` cannot create a database or role
through a CRD (CNPG's declarative objects do not cover external servers), so the
controller runs the SQL itself in a one-shot Kubernetes Job. The Job runs in the
server's namespace — where it can reach a cnpg cluster over short-DNS and read the
admin Secret — connects as the server admin, and applies idempotent SQL: create the
database if absent, create the role if absent, (re)set its password, and make it the
owner. The role password is passed in from a Secret (sourced, generate-once, from
OpenBao) so it never appears in the manifest.

The ``build_*`` functions are pure so the SQL and the Job manifest are unit-testable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .kube import TypedResource

# A safe lowercase SQL identifier (also a CNPG/Postgres-friendly name). Validating
# here lets us inline the database / role names as quoted identifiers without risking
# SQL injection; only the password is parameterised (it is random and opaque).
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

# psql connects with the admin credentials from these env vars (PG* are libpq's).
ADMIN_USER_ENV = "PGUSER"
ADMIN_PASSWORD_ENV = "PGPASSWORD"
NEW_PASSWORD_ENV = "NEW_USER_PASSWORD"  # the role password psql sets via :'pw'


class InvalidIdentifier(ValueError):
    """A database or role name is not a valid lowercase SQL identifier."""


def validate_identifier(value: str, what: str = "identifier") -> str:
    """Return ``value`` if it is a safe lowercase SQL identifier, else raise."""
    if not value or len(value) > 63 or not _IDENTIFIER.match(value):
        raise InvalidIdentifier(
            f"invalid {what} {value!r} (lowercase letters, digits, underscore; <=63 chars)"
        )

    return value


def build_sql(database: str, user: str, init_sql: list[str] | None = None) -> str:
    """The idempotent SQL that creates the database, role, and ownership (pure).

    ``database`` and ``user`` must already be validated identifiers; they are inlined
    as quoted identifiers. The role password is read from the psql ``pw`` variable
    (set from the environment) so it is never part of this string.

    ``init_sql`` is optional app-specific setup (e.g. auxiliary roles, extensions) the
    chart declared on the Database. It runs AFTER the base provisioning, reconnected to
    the just-created database (``\\c``) so it can both create cluster-global roles and
    touch the database itself. The statements run as the server admin (superuser); the
    chart author is responsible for making them idempotent.
    """
    validate_identifier(database, "databaseName")
    validate_identifier(user, "user")

    lines = [
        # CREATE DATABASE cannot run in a transaction or DO block; \gexec runs the
        # generated statement only when the database does not already exist.
        f"SELECT 'CREATE DATABASE \"{database}\"'",
        f"WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{database}')\\gexec",
        "DO $do$",
        "BEGIN",
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{user}') THEN",
        f'    CREATE ROLE "{user}" LOGIN;',
        "  END IF;",
        "END",
        "$do$;",
        f"ALTER ROLE \"{user}\" WITH LOGIN PASSWORD :'pw';",
        f'GRANT ALL PRIVILEGES ON DATABASE "{database}" TO "{user}";',
        f'ALTER DATABASE "{database}" OWNER TO "{user}";',
    ]

    if init_sql:
        # Reconnect to the created database so init SQL can also install extensions
        # there; CREATE ROLE / GRANT remain cluster-global regardless of the database.
        lines.append(f'\\c "{database}"')
        lines.extend(init_sql)

    return "\n".join(lines)


def build_command(database: str, user: str, init_sql: list[str] | None = None) -> list[str]:
    """The container command (``sh -c``) that pipes the SQL into psql (pure).

    The heredoc delimiter is single-quoted so the shell does not expand the SQL body;
    the role password reaches psql via ``-v pw`` from the environment.
    """
    script = (
        f"psql -v ON_ERROR_STOP=1 -v pw=\"${NEW_PASSWORD_ENV}\" -d postgres <<'EOSQL'\n"
        f"{build_sql(database, user, init_sql)}\n"
        "EOSQL"
    )

    return ["/bin/sh", "-c", script]


def _secret_env(name: str, secret: str, key: str) -> client.V1EnvVar:
    return client.V1EnvVar(
        name=name,
        value_from=client.V1EnvVarSource(
            secret_key_ref=client.V1SecretKeySelector(name=secret, key=key),
        ),
    )


@dataclass
class ProvisionJob(TypedResource):
    """A one-shot Job that creates a database + role on a server (create-only).

    Jobs are immutable once created, so ``apply`` only creates it when absent (like a
    submitted Argo Workflow). ``succeeded`` / ``failed`` read the Job's status so the
    reconciler can poll and gate readiness.
    """

    MANAGED_BY = "adomi-platform-controller"
    # Stamps the hash of the provisioning command so apply() can recreate the (otherwise
    # immutable) Job when the desired SQL changes — e.g. when initSql is edited.
    HASH_ANNOTATION = "platform.adomi.io/provision-hash"

    name: str
    namespace: str  # the server's namespace (cnpg cluster / admin Secret live here)
    image: str  # an image with psql (e.g. postgres:16)
    host: str
    port: int
    admin_secret: str  # Secret with admin "username"/"password"
    database: str
    user: str
    user_secret: str  # Secret holding the new role's password
    user_secret_key: str = "password"
    admin_username_key: str = "username"
    admin_password_key: str = "password"
    ssl_mode: str = ""
    init_sql: list[str] = field(default_factory=list)
    backoff_limit: int = 4
    labels: dict[str, str] = field(default_factory=dict)

    def _command(self) -> list[str]:
        return build_command(self.database, self.user, self.init_sql)

    def _hash(self) -> str:
        return hashlib.sha256(self._command()[-1].encode()).hexdigest()[:16]

    @staticmethod
    def _api() -> client.BatchV1Api:
        return client.BatchV1Api()

    def _env(self) -> list[client.V1EnvVar]:
        env = [
            client.V1EnvVar(name="PGHOST", value=self.host),
            client.V1EnvVar(name="PGPORT", value=str(self.port)),
            _secret_env(ADMIN_USER_ENV, self.admin_secret, self.admin_username_key),
            _secret_env(ADMIN_PASSWORD_ENV, self.admin_secret, self.admin_password_key),
            _secret_env(NEW_PASSWORD_ENV, self.user_secret, self.user_secret_key),
        ]

        if self.ssl_mode:
            env.append(client.V1EnvVar(name="PGSSLMODE", value=self.ssl_mode))

        return env

    def _body(self) -> client.V1Job:
        meta_labels = {"app.kubernetes.io/managed-by": self.MANAGED_BY, **self.labels}

        container = client.V1Container(
            name="provision",
            image=self.image,
            command=self._command(),
            env=self._env(),
        )

        return client.V1Job(
            metadata=client.V1ObjectMeta(
                name=self.name,
                namespace=self.namespace,
                labels=meta_labels,
                annotations={self.HASH_ANNOTATION: self._hash()},
            ),
            spec=client.V1JobSpec(
                backoff_limit=self.backoff_limit,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=meta_labels),
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        containers=[container],
                    ),
                ),
            ),
        )

    def _read(self):
        return self._api().read_namespaced_job(self.name, self.namespace)

    def _create(self):
        return self._api().create_namespaced_job(self.namespace, self._body())

    def _patch(self):
        # Jobs are immutable; an existing Job is left as-is (create-only).
        return None

    def apply(self) -> None:
        """Create the Job if absent; recreate it when the provisioning SQL changed.

        Jobs are immutable, so a Job whose stamped hash no longer matches the desired
        command is deleted; the next reconcile (re)creates it fresh. An unchanged Job is
        left untouched (so a completed provision is not needlessly re-run)."""
        try:
            existing = self._read()
        except ApiException as exc:
            if exc.status != 404:
                raise

            self._create()
            return

        annotations = (existing.metadata.annotations or {}) if existing.metadata else {}

        if annotations.get(self.HASH_ANNOTATION) != self._hash():
            self.delete(self.name, self.namespace)

    @classmethod
    def read(cls, name: str, namespace: str) -> dict | None:
        """Return the Job as a dict, or None if it does not exist."""
        try:
            job = cls._api().read_namespaced_job(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None

            raise

        return cls._api().api_client.sanitize_for_serialization(job)

    @staticmethod
    def _condition(job: dict | None, kind: str) -> bool:
        for cond in ((job or {}).get("status") or {}).get("conditions") or []:
            if cond.get("type") == kind and cond.get("status") == "True":
                return True

        return False

    @classmethod
    def succeeded(cls, job: dict | None) -> bool:
        """True once the Job has completed successfully."""
        return cls._condition(job, "Complete") or bool(
            ((job or {}).get("status") or {}).get("succeeded")
        )

    @classmethod
    def failed(cls, job: dict | None) -> bool:
        """True once the Job has exhausted its retries (terminal failure)."""
        return cls._condition(job, "Failed")

    @classmethod
    def delete(cls, name: str, namespace: str) -> None:
        """Delete the Job and its pods (no-op if already gone)."""
        try:
            cls._api().delete_namespaced_job(
                name,
                namespace,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status != 404:
                raise

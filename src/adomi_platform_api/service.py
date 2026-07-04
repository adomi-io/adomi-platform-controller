"""ClientService: turn a resource spec into a CR and commit it to the client repo."""

from __future__ import annotations

import yaml

from adomi_platform_schema import (
    SchemaError,
    build_manifest,
    repo_path,
    resource_for_plural,
    validate_name,
)

from .git import GitWriter


class NotFoundError(LookupError):
    """The addressed CR has no committed manifest in the client repo."""


class ClientService:
    """Commits/removes platform CRs in a customer's client repo (one repo per client)."""

    def __init__(self, writer: GitWriter, *, namespace_prefix: str, managed_by: str, git_mode: str):
        self.writer = writer
        self.namespace_prefix = namespace_prefix
        self.managed_by = managed_by
        self.git_mode = git_mode

    def commit(self, client: str, plural: str, name: str, spec: dict, *, labels=None) -> dict:
        """Build the CR for ``spec`` and commit it; returns a write result."""
        resource_for_plural(plural)  # validates the plural
        validate_name(client, "customer")
        validate_name(name, "name")

        manifest = build_manifest(
            plural,
            name,
            spec,
            client=client,
            namespace_prefix=self.namespace_prefix,
            managed_by=self.managed_by,
            labels=labels,
        )
        content = yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
        path = repo_path(plural, name)
        message = f"Upsert {manifest['kind']} {name}"

        result = self.writer.apply_manifest(client, path, content, message, mode=self.git_mode)

        return {
            "repo": client,
            "path": path,
            "namespace": manifest["metadata"]["namespace"],
            **result,
        }

    def set_variable(self, client: str, plural: str, name: str, var: str, value: str) -> dict:
        """Set a plain scoped variable on an existing CR (read-modify-write)."""
        return self._mutate_variables(client, plural, name, var, value=value, remove=False)

    def remove_variable(self, client: str, plural: str, name: str, var: str) -> dict:
        """Remove a scoped variable from an existing CR (read-modify-write)."""
        return self._mutate_variables(client, plural, name, var, value="", remove=True)

    def _mutate_variables(self, client, plural, name, var, *, value, remove) -> dict:
        rt = resource_for_plural(plural)
        validate_name(client, "customer")
        validate_name(name, "name")
        if not var or not var.strip():
            raise SchemaError("variable name is required")
        var = var.strip()

        path = repo_path(plural, name)
        current = self.writer.read_manifest(client, path)

        if current is None:
            raise NotFoundError(f"{rt.kind} {name!r} has no committed manifest")

        manifest = yaml.safe_load(current) or {}
        spec = manifest.setdefault("spec", {})
        entries = [v for v in (spec.get("variables") or []) if v.get("name") != var]

        if remove:
            action = f"Remove variable {var} from {rt.kind} {name}"
        else:
            entries.append({"name": var, "value": value})
            action = f"Set variable {var} on {rt.kind} {name}"

        if entries:
            spec["variables"] = entries
        else:
            spec.pop("variables", None)

        content = yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
        result = self.writer.apply_manifest(client, path, content, action, mode=self.git_mode)

        return {"repo": client, "path": path, **result}

    def variables(self, client: str, plural: str, name: str) -> list[dict]:
        """The plain variables currently committed on a CR (empty when none)."""
        resource_for_plural(plural)
        validate_name(client, "customer")
        validate_name(name, "name")

        current = self.writer.read_manifest(client, repo_path(plural, name))

        if current is None:
            raise NotFoundError(f"{plural}/{name} has no committed manifest")

        manifest = yaml.safe_load(current) or {}

        return list((manifest.get("spec") or {}).get("variables") or [])

    def remove(self, client: str, plural: str, name: str) -> dict:
        """Remove a resource's CR from the client repo."""
        rt = resource_for_plural(plural)
        validate_name(client, "customer")
        validate_name(name, "name")

        path = repo_path(plural, name)
        message = f"Delete {rt.kind} {name}"

        result = self.writer.delete_manifest(client, path, message, mode=self.git_mode)

        return {"repo": client, "path": path, **result}

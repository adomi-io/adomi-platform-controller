"""ClientService: turn a resource spec into a CR and commit it to the client repo."""

from __future__ import annotations

import yaml

from adomi_platform_schema import build_manifest, repo_path, resource_for_plural, validate_name

from .git import GitWriter


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

    def remove(self, client: str, plural: str, name: str) -> dict:
        """Remove a resource's CR from the client repo."""
        rt = resource_for_plural(plural)
        validate_name(client, "customer")
        validate_name(name, "name")

        path = repo_path(plural, name)
        message = f"Delete {rt.kind} {name}"

        result = self.writer.delete_manifest(client, path, message, mode=self.git_mode)

        return {"repo": client, "path": path, **result}
